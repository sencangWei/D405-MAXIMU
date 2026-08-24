from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rsusb_builder_uses_pkg_config_compiler_include_path() -> None:
    builder = (ROOT / "scripts/build_librealsense_rsusb.sh").read_text(
        encoding="utf-8"
    )

    assert "pkg-config --cflags-only-I libusb-1.0" in builder
    assert "pkg-config --variable=includedir libusb-1.0" not in builder
    assert '[[ ! -f "$LIBUSB_INC/libusb.h" ]]' in builder
