FROM umi-ego-vio:product-v1-20260824

USER root

# Calibration preview annotations are Chinese.  The product-1 image does not
# ship a CJK font, and DejaVu silently renders those labels as square boxes.
# Bundle the font in the derived image so calibration remains self-contained
# on a customer host instead of depending on host font bind mounts.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Preserve the exact container-1 preflight before installing the device-2
# identity checks.
RUN cp /usr/local/bin/umi-container-preflight \
      /usr/local/bin/umi-container-preflight-product1

# The product-1 code already supports the 63-byte STM32 packet and gripper
# recording. Device 2 changes only the calibrated angle-to-gap profile.
COPY gripper/umi_manual_gripper_c48df736_20260826_v4.yaml \
  /home/robot/ego_vio_humble/config/gripper/umi_manual_gripper_c48df736_20260826_v4.yaml
COPY gripper/umi_manual_gripper_c48df736_20260826_v4.yaml \
  /home/robot/ego_vio_humble/config/gripper/umi_manual_gripper_20260824.yaml
COPY calibration_assets/aprilgrid_6x6_35mm.yaml \
  /home/robot/ego_vio_humble/config/aprilgrid_6x6_35mm.yaml
COPY formal_runtime_calibration /opt/umi/formal_runtime_calibration
COPY vendor/aprilgrid-0.5.0-py3-none-any.whl /opt/umi/vendor/
RUN python3 -m pip install --no-deps \
      /opt/umi/vendor/aprilgrid-0.5.0-py3-none-any.whl \
    && python3 -c 'import aprilgrid, importlib.metadata as m; assert m.version("aprilgrid") == "0.5.0"'

# Container 1's calibration collector assumes Noto CJK is installed. Keep the
# proven preview fallback for robustness, but the preflight below now requires
# the bundled Noto CJK font so Chinese labels may not silently degrade.
COPY docker/patch_calibration_preview.py /opt/umi/patch_calibration_preview.py
COPY docker/patch_calibration_io.py /opt/umi/patch_calibration_io.py
COPY docker/patch_calibration_timing_message.py /opt/umi/patch_calibration_timing_message.py
COPY docker/patch_candidate_live_io.py /opt/umi/patch_candidate_live_io.py
COPY docker/build_candidate_live_runner.py /opt/umi/build_candidate_live_runner.py
RUN python3 /opt/umi/patch_calibration_preview.py \
    && python3 /opt/umi/patch_calibration_io.py \
    && python3 /opt/umi/patch_calibration_timing_message.py \
    && python3 /opt/umi/patch_candidate_live_io.py \
      --bridge /home/robot/ego_vio_humble/ego_vio/vio/openvins_ros2_bridge.py \
      --capture /home/robot/ego_vio_humble/scripts/capture_d405_720p_rgb_stereo_ir.py \
      --viewer /home/robot/ego_vio_humble/scripts/rerun_vio_viewer.py \
      --visualizer /home/robot/ego_vio_humble/ego_vio/visualizer/rerun_viz.py \
    && python3 /opt/umi/build_candidate_live_runner.py \
      /home/robot/ego_vio_humble/run_vins_realtime.sh \
      /home/robot/ego_vio_humble/run_vins_realtime_candidate.sh \
    && python3 -m py_compile \
      /home/robot/ego_vio_humble/scripts/collect_calib_data.py \
      /home/robot/ego_vio_humble/scripts/convert_to_kalibr_bag.py \
      /home/robot/ego_vio_humble/ego_vio/vio/openvins_ros2_bridge.py \
      /home/robot/ego_vio_humble/ego_vio/visualizer/rerun_viz.py \
      /home/robot/ego_vio_humble/scripts/capture_d405_720p_rgb_stereo_ir.py \
      /home/robot/ego_vio_humble/scripts/rerun_vio_viewer.py

# Container 1 hard-coded its own camera/IMU td only for the training-data
# gripper-camera association. Device 2 must consume the signed calibration td.
# This is the sole modification to the container-1 D405 capture source.
RUN sed -i \
      's/^PRODUCT_CAMERA_IMU_TD_S = -0\.009312$/PRODUCT_CAMERA_IMU_TD_S = float(os.environ["EGO_VIO_CAMERA_IMU_TD_S"])/' \
      /home/robot/ego_vio_humble/scripts/capture_d405_720p_rgb_stereo_ir.py \
    && grep -Fx \
      'PRODUCT_CAMERA_IMU_TD_S = float(os.environ["EGO_VIO_CAMERA_IMU_TD_S"])' \
      /home/robot/ego_vio_humble/scripts/capture_d405_720p_rgb_stereo_ir.py

COPY device_manifest.yaml /opt/umi/device_manifest.yaml
COPY docker/device2_d405_control.py /usr/local/bin/umi-device2-d405-control
COPY docker/run_slam_postprocess_configurable.sh \
  /usr/local/bin/umi-run-slam-postprocess-configurable
COPY docker/container_preflight_device2_d405.sh /usr/local/bin/umi-container-preflight
RUN chmod 755 /usr/local/bin/umi-device2-d405-control \
      /home/robot/ego_vio_humble/run_vins_realtime_candidate.sh \
      /usr/local/bin/umi-run-slam-postprocess-configurable \
      /usr/local/bin/umi-container-preflight

LABEL org.umi.base-image="umi-ego-vio:product-v1-20260824" \
      org.umi.device-set-id="UMI_DEVICE_02_C48DF736" \
      org.umi.camera-model="D405" \
      org.umi.d435i-runtime="excluded" \
      org.umi.release-id="UMI_DEVICE2_D405_PRODUCT_V1_20260829" \
      org.umi.release-status="accepted"

ENV EGO_VIO_DEVICE_SET_ID=UMI_DEVICE_02_C48DF736 \
    EGO_VIO_RELEASE_ID=UMI_DEVICE2_D405_PRODUCT_V1_20260829 \
    EGO_VIO_GRIPPER_CALIBRATION=/home/robot/ego_vio_humble/config/gripper/umi_manual_gripper_c48df736_20260826_v4.yaml \
    EGO_VIO_CAPTURE_RUNTIME=/home/robot/ego_vio_humble \
    EGO_VIO_RSUSB_RUNTIME=/home/robot/ego_vio_humble \
    PYTHONUNBUFFERED=1

USER 1000:1000
WORKDIR /home/robot/ego_vio_humble
CMD ["umi-device2-d405-control", "status"]
