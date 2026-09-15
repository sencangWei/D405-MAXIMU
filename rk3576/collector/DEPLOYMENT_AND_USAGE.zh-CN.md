# UMI RK3576 采集端部署与使用手册

本文面向拿到一块新 RK3576 或一台新管理电脑的开发、测试和运维人员。采集端负责 D405 RGB/双 IR、STM32 IMU/磁编码器数据采集、720p RGB 预览、板内保存以及按现有 EGO App 合同转存；本仓库不修改 App 代码。

## 1. 系统边界与数据格式

- RGB：1280×720、30 Hz，Rockchip MPP H.265，供预览和辅助检查。
- 左/右 IR：各 1280×720、30 Hz，分别保存为 H.265，供双目后处理。
- 预览：RGB 1280×720、目标 15 Hz，兼容 EGO 的 `stereo.mjpg` 预览合同。
- STM32：保留每包 63 字节的 `stm32_combined_v1` 原始包及时间戳索引；IMU 和磁编码器处于同一 MCU 时钟域。
- 转存：沿用 EGO 的 M02 Catalog、不可变快照、签名请求、HTTP Range/ETag 续传与最终回执合同。

双 IR H.265 是有损压缩，manifest 中明确写为 `lossless=false`。它通过了绑定设备上的采集、解码和已有 SLAM 对照，但不等价于 DB3/FFV1 原始无损基线。SLAM 双目输入必须是左 IR + 右 IR，不能把 RGB + 左 IR 当成双目。

每个封存会话至少包含 RGB、左右 IR、STM32 原始流、各流时间索引、元数据和完整性 manifest。只有帧率、时间戳、序列、STM32 标志位和编码器队列检查全部通过的会话才会进入 Catalog；失败会话不会伪装成可转存数据。

## 2. 版本与主机要求

正式安装物是不可变的 `rk3576-umi-<版本>.tar.gz`，不要直接把开发目录当正式版使用。目标环境为 Ubuntu 24.04 aarch64、Python 3.12，且需要 Rockchip GStreamer 插件 `mpph265enc`。包内固定 RSUSB 版 librealsense 运行库和 EGO App 合同运行时。

安装前在目标板执行：

```bash
uname -m
python3 --version
gst-inspect-1.0 mpph265enc >/dev/null && echo MPP_OK
sha256sum rk3576-umi-0.2.3.tar.gz
```

0.2.3 正式包 SHA-256 是 `f2ebbb71e87fd00a32e8acb6f16a83c5222711fc235f64ee9b37367c830104f1`，大小 11164690 字节；必须同时与仓库的 `RELEASE_PROVENANCE.json` 一致。源码分支是 `umi-rk3576-collector-adapter`，该包 manifest 绑定源码提交 `d0ba8ed61a5149c9c97742165f8f088b10adad49`。

## 3. 查找并固定设备身份

正式配置必须固定四项身份，不能依赖易变的 `/dev/videoN` 或 `/dev/ttyUSB0`：

1. `UMI_DEVICE_ID`：本块 RK3576 的业务编号，例如 `umi-rk3576-161`。
2. `UMI_D405_SDK_SERIAL`：librealsense/SDK 看到的 D405 序列号。
3. `UMI_D405_USB_SERIAL`：USB descriptor 序列号。
4. `UMI_STM32_PORT`：CP2102N 的 `/dev/serial/by-id/...` 永久路径。

更换任一传感器后必须重新确认身份，并给新物理组合使用匹配的相机—IMU 外参。相同型号不代表可以沿用另一套设备的标定。

在传感器接好的板上可用以下只读命令核对：

```bash
find /dev/serial/by-id -maxdepth 1 -type l -printf '%f -> %l\n'
for f in /sys/bus/usb/devices/*/serial; do printf '%s: ' "$f"; cat "$f"; done
/home/pi/umi-collector/current/native/bin/umi-rsusb-probe
```

## 4. 安装不可变发布包

在管理电脑上传并登录板子：

```bash
scp rk3576-umi-0.2.3.tar.gz pi@BOARD_IP:/home/pi/
ssh pi@BOARD_IP
```

在板上解包、校验并安装：

```bash
mkdir -p /home/pi/umi-package-0.2.3
tar -xzf /home/pi/rk3576-umi-0.2.3.tar.gz -C /home/pi/umi-package-0.2.3
cd /home/pi/umi-package-0.2.3
sha256sum --strict --check SHA256SUMS
bash check-host.sh
bash install.sh /home/pi/umi-collector
readlink -f /home/pi/umi-collector/current
```

`install.sh` 不覆盖同版本目录；升级会新增 `releases/<版本>` 并原子切换 `current`。不删除旧版本，便于回滚。

如新板暂时没有传感器，只允许在构建阶段显式执行 `UMI_SKIP_PROBE=1 native/build-on-rk3576.sh`。这只跳过构建后的硬件探测，不放宽运行时 preflight，也不能算 HIL 验收。

## 5. 板外配置、密钥与服务

复制模板到发布目录之外：

```bash
mkdir -p /home/pi/.config/umi-recorder/{admin-public-keys,tls}
mkdir -p /home/pi/umi-recordings/recordings-v2/completed
cp /home/pi/umi-collector/current/service/umi-recorder.env.example \
  /home/pi/.config/umi-recorder/umi-recorder.env
chmod 600 /home/pi/.config/umi-recorder/umi-recorder.env
```

编辑环境文件，填入第 3 节的四项固定身份。任何 `CHANGE_ME` 都会令服务安全失败。录制目录默认 `/home/pi/umi-recordings`，状态和 Catalog 默认 `/home/pi/.local/state/umi-recorder`。

Admin 使用 Ed25519 App 公钥和 TLS。私钥只放在被授权的 App/管理电脑，板子只安装 `<key-id>.pub`；TLS 私钥和录制数据都不得提交 Git。公钥目录必须为 0700、文件为 0600。正式证书应由部署环境的证书体系签发；自签名证书只用于受控验收。

安装服务：

```bash
mkdir -p /home/pi/.config/systemd/user /home/pi/.local/bin
cp /home/pi/umi-collector/current/service/umi-preview.service /home/pi/.config/systemd/user/
cp /home/pi/umi-collector/current/service/umi-admin.service /home/pi/.config/systemd/user/
ln -sfn /home/pi/umi-collector/current/bin/recorderctl /home/pi/.local/bin/recorderctl
sudo install -m 0644 /home/pi/umi-collector/current/99-umi-devices.rules /etc/udev/rules.d/99-umi-devices.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo loginctl enable-linger pi
systemctl --user daemon-reload
systemctl --user enable --now umi-preview.service umi-admin.service
```

`enable-linger` 使用户服务在无人 SSH 登录时也能随系统启动；安装后用 `loginctl show-user pi -p Linger` 确认值为 `yes`。

服务只监听板端回环地址：预览 `127.0.0.1:18080`，签名 Admin API `127.0.0.1:18443`。这避免把采集控制和文件接口裸露在局域网；App 侧应复用 EGO 已有的安全通道/代理。临时人工诊断可使用 SSH 隧道：

```bash
ssh -L 18080:127.0.0.1:18080 -L 18443:127.0.0.1:18443 pi@BOARD_IP
```

## 6. 本地检查、采集与预览

先做身份与 preflight：

```bash
source /home/pi/.config/umi-recorder/umi-recorder.env
/home/pi/.local/bin/recorderctl identity --json
/home/pi/.local/bin/recorderctl preflight --json
systemctl --user --no-pager --full status umi-preview.service umi-admin.service
ss -ltnp | grep -E ':(18080|18443)'
```

`preflight` 必须同时确认固定 D405 SDK/USB 身份和 STM32 by-id。缺设备时失败是正确行为，禁止绕过后继续采集。

启动一次 10 分钟采集；`request-id` 必须是新的 UUID：

```bash
JOB_JSON=$(/home/pi/.local/bin/recorderctl start --json \
  --request-id "$(cat /proc/sys/kernel/random/uuid)" --duration 600)
printf '%s\n' "$JOB_JSON"
```

从返回 JSON 取 `job_id`，查询、停止和日志命令为：

```bash
/home/pi/.local/bin/recorderctl status --json --job-id JOB_ID
/home/pi/.local/bin/recorderctl logs --json --job-id JOB_ID --after 0 --limit 100
/home/pi/.local/bin/recorderctl stop --json --job-id JOB_ID
```

时长为 0 表示一直录到 stop/信号。不要把终端里打印出来的 JSON 当成下一条 shell 命令粘贴，否则会出现 `command not found`。

预览状态：

```bash
/home/pi/.local/bin/recorderctl preview-status --json
curl -fsS http://127.0.0.1:18080/healthz
```

预览和正式录制共享相机。录制开始时会做有界预览交接；交接失败会拒绝录制，避免两个进程争抢 D405。D405 启动阶段少量 UVC `-71/EPROTO` 可通过已有预热流程吸收，但持续错误、缺帧或编码 Broken pipe 都是失败，不应封存为合格数据。

## 7. App 适配与转存

UMI 沿用 EGO 已有合同，不要求本仓库改 App：

- `recorderctl_v1`：身份、preflight、开始、状态、停止、日志。
- `preview_mjpeg_v1` / `preview_shared_media_v1`：720p RGB 预览与采集交接。
- `recording_m02_catalog_v1`：列出不可变录制快照。
- `signed_resumable_transfer_v1`：签名请求、Range/ETag 断点续传和最终回执。

App 团队应把 UMI 识别为第三种设备类型，继续使用已有 EGO 控制面和 Admin OpenAPI，不应直接拼接录制目录或绕过最终回执。正式转存完成并验证回执前，板端源数据不得删除。Admin 的精确路径、请求 schema 和签名字段以绑定的 EGO runtime/OpenAPI 为准，避免另造一套私有协议。

紧急人工取数只能作为诊断备份，例如从 Catalog 中确认封存目录后使用 `rsync --partial`；它不等同于 App 的签名转存，也不得触发源数据清理。

## 8. 新设备验收门槛

无传感器时只能完成安装验收：包校验、host check、服务、回环端口、身份输出，以及 start 在 preflight 阶段安全失败。接齐传感器后必须补做 HIL：

1. 身份四项全部匹配且 preflight PASS。
2. 720p RGB 预览达到目标 15 Hz，无长期断流。
3. 至少 10 分钟 RGB/双 IR 约 30 Hz，STM32 约 400 Hz。
4. 无序列缺口、时间戳回退、CRC/帧错误、无效标志和编码队列溢出。
5. 三路 H.265 都能完整解码，左右 IR 帧数/时间配对满足后处理要求。
6. 会话封存、Catalog 查询、签名 Range/ETag 续传和最终回执全部通过。
7. 用该物理组合的标定和外参跑一次 SLAM，并保存验收 JSON 与轨迹图。

参考 `.30` 绑定设备的 20 秒证据为 RGB/左右 IR 各 600 帧、STM32 8001 包，三路约 30.008 Hz、STM32 约 400.005 Hz，相关完整性错误为 0；它证明候选代码在参考硬件上工作，不自动证明另一块板或另一套传感器合格。

## 9. 回滚与排障

查看版本：

```bash
readlink -f /home/pi/umi-collector/current
ls -1 /home/pi/umi-collector/releases
journalctl --user -u umi-preview.service -u umi-admin.service -n 200 --no-pager
```

回滚只切换到已校验且仍存在的旧版本，然后重启服务：

```bash
ln -s /home/pi/umi-collector/releases/OLD_VERSION /home/pi/umi-collector/.current-rollback
mv -Tf /home/pi/umi-collector/.current-rollback /home/pi/umi-collector/current
systemctl --user restart umi-preview.service umi-admin.service
```

常见问题：

- D405 不见：先看 USB sysfs、供电、线材和扩展坞，不要先改 serial。
- STM32 不见：确认 `/dev/serial/by-id`、`dialout` 组和 udev rule。
- 服务反复重启：检查环境文件是否仍有 `CHANGE_ME`、公钥目录权限、TLS 文件和 journal。
- `RGB encoder ... Broken pipe`：检查 `mpph265enc`、系统负载和相机交接日志；失败会话不要用于 SLAM。
- 容量不足：以真实多流码率和预留空间计算时长；先转存并确认最终回执，再按受控流程删除。
- 断电/进程崩溃：控制器会按 boot ID、PID、启动 ticks 和 publication ledger 恢复；仍需检查该会话最终状态，不能手工把 `interrupted` 改成 `PASSED`。

发布、升级或回滚后都要保存：版本、源码提交、包 SHA-256、原生二进制 SHA-256、板端身份、服务状态、preflight/HIL 结果和失败日志。只有这些证据齐全，才能把“已安装”升级为“该物理设备已验收”。
