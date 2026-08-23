import subprocess
import sys
import unittest
from pathlib import Path

from tools.example_imu_encoder import build_parser


class ExampleScriptTests(unittest.TestCase):
    def test_help_is_valid_for_stm32_or_esp32_device(self) -> None:
        help_text = " ".join(build_parser().format_help().split())

        self.assertIn("IMU and AS5047P acquisition device", help_text)
        self.assertNotIn("CP2102N serial port", help_text)

    def test_help_describes_required_serial_port(self) -> None:
        script = Path(__file__).with_name("example_imu_encoder.py")

        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--port", result.stdout)
        self.assertIn("921600", result.stdout)


if __name__ == "__main__":
    unittest.main()
