
import cv2

# 改成实际确认过的 ISP 视频节点
DEVICE = "/dev/video11"

# 使用 GStreamer 将 NV12 转为 BGR
pipeline = (
    f"v4l2src device={DEVICE} ! "
    "video/x-raw,format=NV12,width=1920,height=1080 ! "
    "videoconvert ! videoscale ! "
    "video/x-raw,format=BGR,width=640,height=360 ! "
    "appsink drop=true max-buffers=1 sync=false"
)

cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

if not cap.isOpened():
    raise RuntimeError("IMX415 camera open failed")

try:
    ok, frame = cap.read()

    if not ok:
        raise RuntimeError("IMX415 frame capture failed")

    cv2.imwrite("opencv_imx415.jpg", frame)

    print("Image size:", frame.shape)
    print("Camera capture successful")
finally:
    cap.release()