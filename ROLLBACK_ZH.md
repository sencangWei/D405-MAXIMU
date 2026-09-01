# 第二套 UMI 产品 V1.0.2 回滚

正式发布不会删除候选代码、候选镜像或原始标定数据。

本次只更换夹爪机械壳体标定；相机—IMU、world-Z、VINS与后处理代码不变。

回滚目标：

- 上一正式工程：`/home/robot/releases/umi_device2_d405_product_1.0.0-20260829`
- 上一正式镜像：`umi-ego-vio:device2-c48df736-d405-product-v1-20260829`
- 上一正式镜像 ID：`sha256:9dda11f65aedf564bc244dae4cc2c2411627c590ca170445827f7fa27aabdcb9`
- 上一夹爪标定：`UMI_MANUAL_GRIPPER_C48DF736_20260826_V4`

触发条件：新镜像完整性、设备身份、夹爪角度/开合距离、ROS图、传感器频率、
时间戳、轨迹、录制或后处理任一正式验收失败。

回滚是一次显式生产变更。先停止当前命令，再执行以下完整流程。仅重打
`docker-2` 标签不够，因为 V1.0.2 启动器默认使用版本化镜像。

```bash
OLD_RELEASE=/home/robot/releases/umi_device2_d405_product_1.0.0-20260829
OLD_IMAGE=umi-ego-vio:device2-c48df736-d405-product-v1-20260829
OLD_ID=sha256:9dda11f65aedf564bc244dae4cc2c2411627c590ca170445827f7fa27aabdcb9
DATA_ROOT=/home/robot/umi_ego_vio_data_device2_c48df736
THIS_RELEASE=/home/robot/releases/umi_device2_d405_product_1.0.2-20260901
BACKUP=$THIS_RELEASE/rollback/active_runtime_calibration_v1_20260829

test "$(docker image inspect "$OLD_IMAGE" --format '{{.Id}}')" = "$OLD_ID"
test -x "$OLD_RELEASE/umi-device2-d405.sh"
test "$(sha256sum "$BACKUP/manifest.yaml" | awk '{print $1}')" = \
  56586f0a98e83171f86ff48c756bef87f3739e80dc8c4dca8692bf8413f333bd
test "$(sha256sum "$BACKUP/vins_config.yaml" | awk '{print $1}')" = \
  3f47e90f838aff2e4770eecccc5bebe29b29fd07833576ab8568cf6bd693db36
test "$(sha256sum "$BACKUP/left.yaml" | awk '{print $1}')" = \
  52941d0724ecac8a59c3daeb494ecc5bbd94b7d063983f9b2944346d53f27b21
test "$(sha256sum "$BACKUP/right.yaml" | awk '{print $1}')" = \
  52941d0724ecac8a59c3daeb494ecc5bbd94b7d063983f9b2944346d53f27b21
test "$(sha256sum "$BACKUP/device_config.yaml" | awk '{print $1}')" = \
  2bd4311e229df57722cd956853551131415a3fdd4ae920a14853136d71146973
grep -Fxq 'release_id: UMI_DEVICE2_D405_PRODUCT_V1_20260829' \
  "$BACKUP/manifest.yaml"

docker tag "$OLD_IMAGE" umi-ego-vio:docker-2
if test -d "$DATA_ROOT/active_runtime_calibration"; then
  mv "$DATA_ROOT/active_runtime_calibration" \
    "$DATA_ROOT/active_runtime_calibration_rolled_back_from_v1_0_2_$(date +%Y%m%d_%H%M%S)"
fi
cp -a "$BACKUP" "$DATA_ROOT/active_runtime_calibration"

UMI_DEVICE2_D405_IMAGE="$OLD_IMAGE" "$OLD_RELEASE/umi-device2-d405.sh" software-check
UMI_DEVICE2_D405_IMAGE="$OLD_IMAGE" "$OLD_RELEASE/umi-device2-d405.sh" hardware-check
UMI_DEVICE2_D405_IMAGE="$OLD_IMAGE" "$OLD_RELEASE/umi-device2-d405.sh" status
```

三条检查全部 PASS 才算回滚完成。不要删除 V1.0.2、V1.0.1、原始盲测或
被移走的运行标定目录。
