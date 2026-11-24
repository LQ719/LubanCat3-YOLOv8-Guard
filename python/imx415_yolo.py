
import argparse
import time
import cv2

from rknnlite.api import RKNNLite

# 导入之前图片检测时已经验证成功的函数
from yolo_lubancat import detect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--device", default="/dev/video11"
    )
    parser.add_argument(
        "--model",
        default="../model/yolov8n_rk3576_i8.rknn"
    )
    parser.add_argument(
        "--frames", type=int, default=150
    )
    args = parser.parse_args()

    # IMX415 经 ISP 输出 NV12，
    # 通过 GStreamer 转为 OpenCV BGR
    pipeline = (
        f"v4l2src device={args.device} ! "
        "video/x-raw,format=NV12,"
        "width=1920,height=1080 ! "
        "videoconvert ! videoscale ! "
        "video/x-raw,format=BGR,"
        "width=640,height=360 ! "
        "appsink drop=true max-buffers=1 sync=false"
    )

    cap = cv2.VideoCapture(
        pipeline, cv2.CAP_GSTREAMER
    )

    if not cap.isOpened():
        raise RuntimeError("Cannot open IMX415")

    rknn = RKNNLite()

    try:
        if rknn.load_rknn(args.model) != 0:
            raise RuntimeError("RKNN load failed")

        if rknn.init_runtime() != 0:
            raise RuntimeError("NPU init failed")

        print("IMX415 + YOLOv8 started")

        start = time.perf_counter()
        count = 0

        for i in range(args.frames):
            ok, frame = cap.read()
            if not ok:
                print("Camera read failed")
                break

            # 调用之前已经跑通的 NPU 检测逻辑
            result, infer_ms, objects = detect(
                frame, rknn
            )

            count += 1

            if count % 10 == 0:
                print(
                    f"Frame {count}, "
                    f"Objects {objects}, "
                    f"NPU {infer_ms:.1f} ms"
                )

            # 每30帧保存一次检测图片
            if count % 30 == 0:
                cv2.imwrite(
                    "imx415_yolo_result.jpg",
                    result
                )

        if count:
            cv2.imwrite(
                "imx415_yolo_result.jpg",
                result
            )
            elapsed = time.perf_counter() - start
            print(f"End-to-end FPS: {count/elapsed:.2f}")
            print("Detection complete")

    finally:
        cap.release()
        rknn.release()


if __name__ == "__main__":
    main()