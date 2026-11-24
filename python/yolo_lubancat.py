
import argparse
import time

import cv2
import numpy as np

from rknnlite.api import RKNNLite
from py_utils.coco_utils import COCO_test_helper
import yolov8 as yolo


# 使用 NumPy 实现 DFL，避免板端安装 PyTorch
def dfl_numpy(position):
    n, c, h, w = position.shape
    bins = c // 4

    x = position.reshape(n, 4, bins, h, w)
    x = x.astype(np.float32)

    x -= np.max(x, axis=2, keepdims=True)
    prob = np.exp(x)
    prob /= np.sum(prob, axis=2, keepdims=True)

    indices = np.arange(bins, dtype=np.float32)
    indices = indices.reshape(1, 1, bins, 1, 1)

    return np.sum(prob * indices, axis=2)


# 替换官方示例中的 PyTorch DFL 函数
yolo.dfl = dfl_numpy


def detect(frame, rknn):
    helper = COCO_test_helper(enable_letter_box=True)

    # 预处理：缩放、填充至 640×640
    img = helper.letter_box(
        frame.copy(),
        new_shape=(640, 640),
        pad_color=(0, 0, 0)
    )

    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    # NPU 推理
    t0 = time.perf_counter()
    # 增加 batch 维度
    if img.ndim == 3:
        img = np.expand_dims(img, axis=0)

    print("RKNN input shape:", img.shape)

    # NPU 推理
    outputs = rknn.inference(inputs=[img])
    
    infer_ms = (time.perf_counter() - t0) * 1000

    if outputs is None:
        raise RuntimeError("NPU inference failed")

    # 官方 YOLOv8 后处理
    boxes, classes, scores = yolo.post_process(outputs)

    result = frame.copy()

    if boxes is None:
        return result, infer_ms, 0

    boxes = helper.get_real_box(boxes)

    # 绘制目标检测框
    for box, cls, score in zip(boxes, classes, scores):
        x1, y1, x2, y2 = [int(v) for v in box]

        label = f"{yolo.CLASSES[int(cls)]} {score:.2f}"

        cv2.rectangle(
            result, (x1, y1), (x2, y2),
            (0, 255, 0), 2
        )

        cv2.putText(
            result, label,
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6, (0, 0, 255), 2
        )

    return result, infer_ms, len(boxes)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model",
        default="../model/yolov8n_rk3576_i8.rknn"
    )

    parser.add_argument(
        "--image",
        default="../model/bus.jpg"
    )

    parser.add_argument(
        "--camera", type=int, default=None
    )

    parser.add_argument(
        "--output", default="result.jpg"
    )

    parser.add_argument(
        "--frames", type=int, default=150
    )

    args = parser.parse_args()

    # 初始化 RKNN
    rknn = RKNNLite()

    if rknn.load_rknn(args.model) != 0:
        raise RuntimeError("Failed to load RKNN model")

    if rknn.init_runtime() != 0:
        rknn.release()
        raise RuntimeError("Failed to initialize NPU")

    try:
        # 模式一：图片检测
        if args.camera is None:
            frame = cv2.imread(args.image)

            if frame is None:
                raise RuntimeError("Cannot read input image")

            result, ms, count = detect(frame, rknn)
            cv2.imwrite(args.output, result)

            print(f"Objects detected: {count}")
            print(f"NPU inference: {ms:.2f} ms")
            print(f"Saved: {args.output}")

        # 模式二：USB 摄像头检测
        else:
            cap = cv2.VideoCapture(
                args.camera, cv2.CAP_V4L2
            )

            if not cap.isOpened():
                raise RuntimeError("Cannot open camera")

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            total = 0
            t_start = time.perf_counter()

            try:
                for i in range(args.frames):
                    ok, frame = cap.read()

                    if not ok:
                        print("Camera frame read failed")
                        break

                    result, ms, count = detect(frame, rknn)
                    total += 1

                    if total % 10 == 0:
                        print(
                            f"Frame {total}: "
                            f"{count} objects, "
                            f"NPU {ms:.2f} ms"
                        )

                    if total % 30 == 0:
                        cv2.imwrite(args.output, result)

                if total:
                    cv2.imwrite(args.output, result)
                    elapsed = time.perf_counter() - t_start
                    print(f"End-to-end FPS: {total / elapsed:.2f}")
                    print(f"Saved: {args.output}")
            finally:
                cap.release()
    finally:
        rknn.release()


if __name__ == "__main__":
    main()