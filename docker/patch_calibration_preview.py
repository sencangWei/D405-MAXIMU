#!/usr/bin/env python3
"""Apply the proven preview-font fallback without importing D435i changes."""

from pathlib import Path


TARGET = Path("/home/robot/ego_vio_humble/scripts/collect_calib_data.py")

OLD_FONT = (
    'PREVIEW_FONT_PATH = '
    '"/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"'
)
NEW_FONT = '''PREVIEW_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)'''

OLD_INIT = '''    if preview_enabled:
        try:
            cv2.namedWindow("ego_vio calibration camera", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("ego_vio calibration camera", 1280, 720)
            preview_font = ImageFont.truetype(PREVIEW_FONT_PATH, 25)
            print("[预览] 已打开实时相机窗口，按 q 可在完成当前保存后退出")
        except Exception as e:
            preview_enabled = False
            print(f"[预览] 无法打开窗口，继续无预览采集: {e}")'''

NEW_INIT = '''    if preview_enabled:
        try:
            cv2.namedWindow("ego_vio calibration camera", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("ego_vio calibration camera", 1280, 720)
        except Exception as e:
            preview_enabled = False
            print(f"[预览] 无法打开窗口，继续无预览采集: {e}")
        if preview_enabled:
            for font_path in PREVIEW_FONT_PATHS:
                try:
                    preview_font = ImageFont.truetype(font_path, 25)
                    break
                except OSError:
                    continue
            if preview_font is None:
                # Missing annotation fonts must not disable the camera view.
                preview_font = ImageFont.load_default()
                print("[预览] 警告：未找到中文字体，画面保留但文字可能显示不完整")
            print("[预览] 已打开实时相机窗口，按 q 可在完成当前保存后退出")'''


def replace_exactly_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source block, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    # A whole-file copy from the temporary D435i branch is forbidden.
    forbidden = ("--disable-emitter", "emitter_enabled")
    present = [token for token in forbidden if token in text]
    if present:
        raise SystemExit(f"unexpected D435i-only tokens before patch: {present}")

    text = replace_exactly_once(text, OLD_FONT, NEW_FONT, "font paths")
    text = replace_exactly_once(text, OLD_INIT, NEW_INIT, "preview init")
    TARGET.write_text(text, encoding="utf-8")

    patched = TARGET.read_text(encoding="utf-8")
    required = ("PREVIEW_FONT_PATHS", "ImageFont.load_default()")
    if any(token not in patched for token in required):
        raise SystemExit("preview fallback verification failed")
    if any(token in patched for token in forbidden):
        raise SystemExit("D435i-only code entered D405 calibration source")


if __name__ == "__main__":
    main()
