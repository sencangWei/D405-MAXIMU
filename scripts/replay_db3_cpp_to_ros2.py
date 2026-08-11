#!/usr/bin/env python3
"""Compatibility wrapper for the C++ 720p stereo DB3 replay executable."""

import os
import sys


def main() -> None:
    os.execvp(
        "ros2",
        ["ros2", "run", "vins_fusion_ros2", "db3_replay_cpp", *sys.argv[1:]],
    )


if __name__ == "__main__":
    main()
