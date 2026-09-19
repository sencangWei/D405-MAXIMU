set -u
D=/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/20260914_validation_v11_holdout_batch3/group2
M3=$D/fusion/tight/mast3r
SES=/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260914_202348
VINS=/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml
IMUC=/home/robot/ego_vio_humble/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml
OUT=/tmp/claude-1000/tdsweep; mkdir -p $OUT
for TD in -0.040 -0.030 -0.020 -0.015 -0.009109323 -0.005 0.000 0.005 0.010 0.015 0.020 0.030 0.040; do
  TAG=$(echo $TD | tr -d '.-')
  timeout 300 python3 /home/robot/ego_vio_humble/scripts/align_mast3r_scale_with_imu.py \
     --trajectory $M3/trajectory_frames.csv --session $SES --stream infrared_left \
     --body-t-camera-yaml $VINS --imu-calibration $IMUC --td-s $TD \
     --node-stride 10 --max-hop 1 \
     --output $OUT/m_$TAG.csv --report $OUT/r_$TAG.json >/dev/null 2>$OUT/e_$TAG.log \
     && echo "OK   td=$TD" || echo "FAIL td=$TD : $(tail -1 $OUT/e_$TAG.log | head -c 100)"
done
