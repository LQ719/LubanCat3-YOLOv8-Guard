
import gpiod
import atexit

# 必须与你之前成功点亮 LED 的 GPIO 配置一致
CHIP_NUMBER = 1
LINE_OFFSET = 24

# 假设 LED 高电平亮、低电平灭
# 如果你的 LED 是低电平点亮，需要交换这两个值
LED_ON = 1
LED_OFF = 0

# 打开 GPIO 控制器
chip = gpiod.Chip(
    str(CHIP_NUMBER),
    gpiod.Chip.OPEN_BY_NUMBER
)

# 获取 GPIO 线路
led_line = chip.get_line(LINE_OFFSET)

# 配置为输出，初始化为熄灭
led_line.request(
    consumer="yolo_alarm",
    type=gpiod.LINE_REQ_DIR_OUT,
    default_vals=[LED_OFF]
)


def led_on():
    """检测到人员闯入时调用"""
    led_line.set_value(LED_ON)
    print("[LED] 报警开启，LED 点亮", flush=True)


def led_off():
    """人员离开禁区时调用"""
    led_line.set_value(LED_OFF)
    print("[LED] 报警解除，LED 熄灭", flush=True)


def cleanup():
    """程序退出时释放 GPIO"""
    try:
        led_off()
    finally:
        led_line.release()
        chip.close()


atexit.register(cleanup)