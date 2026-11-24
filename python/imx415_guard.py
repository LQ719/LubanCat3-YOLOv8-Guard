#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LubanCat3 + IMX415 + RKNN: 人数统计、禁区检测、LED 报警及抓拍。"""


import os
import sys

ROOT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../..")
)
sys.path.insert(0, ROOT_DIR)

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path


import cv2
import numpy as np
from rknnlite.api import RKNNLite
import sys
from pathlib import Path

# 自动获取 rknn_model_zoo 项目根目录
ROOT_DIR = Path(__file__).resolve().parents[3]

# 永久添加到 Python 模块搜索路径
sys.path.insert(0, str(ROOT_DIR))

# 导入 RKNN 工具
from py_utils.coco_utils import COCO_test_helper
import yolo_lubancat as baseline
from led_control import led_on, led_off

# 原教程中的 yolo_lubancat.py 已经用 NumPy 替换了原版 DFL。
# 复用其 yolov8 后处理，避免影响原来能运行的脚本。
yolo = baseline.yolo

# 用 0~1 比例定义禁区的四个顶点（按画面实际情况调整）
ZONE = [
    (0.28, 0.34), (0.73, 0.34),
    (0.85, 0.91), (0.18, 0.91),
]


def get_zone(width, height):
    return np.array(
        [(round(x * width), round(y * height)) for x, y in ZONE],
        dtype=np.int32
    )


class AlarmFilter:
    def __init__(self, confirm=3, clear=8):
        self.confirm = confirm
        self.clear = clear
        self.hit = 0
        self.miss = 0
        self.active = False

    def update(self, in_zone):
        if in_zone > 0:
            self.hit += 1
            self.miss = 0
            if not self.active and self.hit >= self.confirm:
                self.active = True
                return "ON"
        else:
            self.hit = 0
            self.miss += 1
            if self.active and self.miss >= self.clear:
                self.active = False
                return "OFF"
        return None


def detect_people(frame, rknn, min_score):
    # 直接复用已成功运行的 RKNN 模型预处理与 YOLO 后处理流程
    helper = COCO_test_helper(enable_letter_box=True)
    image = helper.letter_box(
        frame.copy(), new_shape=(640, 640), pad_color=(0, 0, 0)
    )
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # 修改：从 3 维扩展到 4 维
    image = np.expand_dims(image, axis=0)

    print("NPU input shape:", image.shape)

    start = time.perf_counter()
    outputs = rknn.inference(inputs=[image])
    infer_ms = (time.perf_counter() - start) * 1000
    if outputs is None:
        raise RuntimeError("NPU inference returned None")

    boxes, classes, scores = yolo.post_process(outputs)
    if boxes is None:
        return [], infer_ms

    boxes = helper.get_real_box(boxes)
    height, width = frame.shape[:2]
    people = []

    for box, cls, score in zip(boxes, classes, scores):
        # 官方 COCO person 的类别索引为 0
        if int(cls) != 0 or float(score) < min_score:
            continue
        x1, y1, x2, y2 = [int(round(float(v))) for v in box]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width - 1, x2))
        y2 = max(0, min(height - 1, y2))
        if x2 > x1 and y2 > y1:
            people.append((x1, y1, x2, y2, float(score)))

    return people, infer_ms


def is_in_zone(person, polygon):
    x1, y1, x2, y2, _score = person
    foot = (float((x1 + x2) / 2), float(y2))  # 用框底中点近似脚部
    return cv2.pointPolygonTest(polygon, foot, False) >= 0


def annotate(frame, people, polygon, alarm, infer_ms, fps):
    result = frame.copy()
    overlay = result.copy()
    cv2.fillPoly(overlay, [polygon], (20, 50, 160))
    cv2.addWeighted(overlay, 0.15, result, 0.85, 0, result)
    cv2.polylines(result, [polygon], True, (0, 125, 255), 2)

    count = sum(is_in_zone(person, polygon) for person in people)
    for x1, y1, x2, y2, score in people:
        person = (x1, y1, x2, y2, score)
        inside = is_in_zone(person, polygon)
        color = (0, 0, 255) if inside else (0, 210, 0)
        cv2.rectangle(result, (x1, y1), (x2, y2), color, 2)
        cv2.circle(result, ((x1 + x2)//2, y2), 4, color, -1)
        cv2.putText(result, f"person {score:.2f}",
                    (x1, max(16, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.53, color, 2)

    h, w = result.shape[:2]
    cv2.rectangle(result, (0, 0), (w, 67), (30, 30, 38), -1)
    cv2.putText(result, f"Persons: {len(people)}  In Zone: {count}",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (255, 255, 255), 2)
    status = "ALARM ON" if alarm else "SAFE"
    cv2.putText(result, f"{status}  NPU: {infer_ms:.0f}ms  FPS: {fps:.1f}",
                (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.53,
                (0, 80, 255) if alarm else (50, 220, 80), 2)
    return result, count


def save_event(outdir, image, total, zone_count):
    now = datetime.now()
    filename = "intrusion_" + now.strftime("%Y%m%d_%H%M%S_%f") + ".jpg"
    if not cv2.imwrite(str(outdir / filename), image):
        print("WARN: 报警图片保存失败")
        return
    csv_file = outdir / "events.csv"
    header = not csv_file.exists() or csv_file.stat().st_size == 0
    with csv_file.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(["time", "persons", "in_zone", "snapshot"])
        writer.writerow([now.isoformat(timespec="seconds"),
                         total, zone_count, filename])
    print("[抓拍] " + str(outdir / filename), flush=True)

def main():
    parser = argparse.ArgumentParser(
        description="IMX415 + YOLOv8: 人数统计与禁区 LED 报警"
    )
    parser.add_argument("--device", default="/dev/video11")
    parser.add_argument("--model", default="../model/yolov8n_rk3576_i8.rknn")
    parser.add_argument("--frames", type=int, default=150,
                        help="0=持续运行，Ctrl+C 退出")
    parser.add_argument("--score", type=float, default=0.55)
    parser.add_argument("--confirm-frames", type=int, default=3)
    parser.add_argument("--clear-frames", type=int, default=8)
    parser.add_argument("--save-every", type=int, default=30)
    parser.add_argument("--output", default="guard_output")
    args = parser.parse_args()

    if not (0 <= args.score <= 1):
        parser.error("--score 必须在 0 到 1 之间")
    if args.frames < 0 or args.save_every < 0:
        parser.error("--frames 与 --save-every 不能为负数")
    if args.confirm_frames < 1 or args.clear_frames < 1:
        parser.error("确认帧数和清除帧数必须大于 0")

    # 复用原来 imx415_yolo.py 已成功的摄像头采集参数。
    # 如果原来使用了不同的 GStreamer pipeline，请将原来的替换到这里。
    pipeline = (
        f"v4l2src device={args.device} ! "
        "video/x-raw,format=NV12,width=1920,height=1080 ! "
        "videoconvert ! videoscale ! "
        "video/x-raw,format=BGR,width=640,height=360 ! "
        "appsink drop=true max-buffers=1 sync=false"
    )
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        raise RuntimeError(
            f"摄像头无法打开：{args.device}；检查已验证的管线和视频节点"
        )

    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    state = AlarmFilter(args.confirm_frames, args.clear_frames)
    rknn = RKNNLite()

    try:
        if rknn.load_rknn(args.model) != 0:
            raise RuntimeError(f"加载 RKNN 模型失败: {args.model}")
        if rknn.init_runtime() != 0:
            raise RuntimeError("RKNN NPU 初始化失败")

        led_off()  # 报警灯初始保持关闭
        print("IMX415 人数统计 + 区域入侵报警已启动", flush=True)
        start = time.perf_counter()
        frame_id = 0

        while args.frames == 0 or frame_id < args.frames:
            ok, frame = cap.read()
            if not ok:
                print("WARN: 采集视频帧失败", flush=True)
                break

            people, infer_ms = detect_people(frame, rknn, args.score)
            height, width = frame.shape[:2]
            polygon = get_zone(width, height)
            in_zone = sum(is_in_zone(p, polygon) for p in people)

            event = state.update(in_zone)
            if event == "ON":
                led_on()
                print(f"[报警] 禁区闯入！画面{len(people)}人，禁区{in_zone}人",
                      flush=True)
            elif event == "OFF":
                led_off()
                print("[解除报警] 禁区持续无人", flush=True)

            frame_id += 1
            fps = frame_id / max(time.perf_counter() - start, 1e-6)
            result, _ = annotate(
                frame, people, polygon, state.active, infer_ms, fps
            )

            if event == "ON":
                save_event(outdir, result, len(people), in_zone)

            if args.save_every and frame_id % args.save_every == 0:
                cv2.imwrite(str(outdir / "preview.jpg"), result)

            if frame_id % 15 == 0:
                print(
                    f"Frame={frame_id}, Persons={len(people)}, "
                    f"InZone={in_zone}, Alarm={state.active}, "
                    f"NPU={infer_ms:.1f}ms, FPS(avg)={fps:.1f}",
                    flush=True
                )

        if frame_id:
            cv2.imwrite(str(outdir / "preview.jpg"), result)
            print(f"共处理 {frame_id} 帧，结果保存在 {outdir}/", flush=True)

    except KeyboardInterrupt:
        print("\nCtrl+C 已停止", flush=True)
    finally:
        # 即使程序异常退出，也应尽量关灯并释放摄像头和 NPU
        try:
            led_off()
        finally:
            cap.release()
            rknn.release()


if __name__ == "__main__":
    main()
