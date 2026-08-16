#!/usr/bin/env python3
"""录制 VIO 验证数据,带语音/文字提示。

用法:
  python scripts/record_vio_prompt.py --duration 60 --session vio_test_xxx
"""
import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config
from ego_vio.runtime import Runtime


def beep(freq=1000, duration=300):
    """Windows 蜂鸣提示。"""
    try:
        import winsound
        winsound.Beep(freq, duration)
    except Exception:
        pass


def speak(text):
    """语音播报,失败则静默。"""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception:
        pass


def prompt_user(quiet=False):
    """给用户倒计时提示: 先静止,再运动。"""
    if quiet:
        return

    prompts = [
        ("准备,3 秒后保持静止", 1),
        ("2", 1),
        ("1", 1),
        ("保持静止 2 秒,不要动", 2),
        ("开始缓慢运动", 0),
    ]

    for msg, sec in prompts:
        print(f"\n>>> {msg}")
        speak(msg)
        beep()
        if sec > 0:
            time.sleep(sec)

    print("\n>>> 录制中,请保持缓慢、平稳移动,避免遮挡相机...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="设备配置文件路径")
    ap.add_argument("--session", default=None, help="录制会话名")
    ap.add_argument("--duration", type=float, default=60, help="总录制秒数(含提示)")
    ap.add_argument("--quiet", action="store_true", help="关闭语音,只打印文字")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rt = Runtime(cfg, session_name=args.session)
    rt.setup(record=True, visualize=False)
    rt.start()

    try:
        # 设备已开始录制,先给出静止/运动提示
        prompt_user(args.quiet)

        if args.duration > 0:
            timer = threading.Timer(args.duration, rt._stop_evt.set)
            timer.daemon = True
            timer.start()

        rt.run()
    except KeyboardInterrupt:
        pass
    finally:
        rt.stop()
        print(f"\n录制已保存到: {rt.out_dir}")
        if not args.quiet:
            speak("录制结束")


if __name__ == "__main__":
    sys.exit(main())
