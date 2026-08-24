#!/usr/bin/env bash
# Remove inherited ROS/colcon overlays before loading the signed product stack.
# This file is intended to be sourced by product entrypoints.

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset COLCON_CURRENT_PREFIX
unset AMENT_CURRENT_PREFIX
unset PYTHONPATH
unset LD_LIBRARY_PATH
unset PKG_CONFIG_PATH
unset ROS_DISTRO
unset ROS_VERSION
unset ROS_PYTHON_VERSION
