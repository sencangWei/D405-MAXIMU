# RK3576 UMI 采集端 —— 交接文档

> 这份文档是给**下一个接手的人或 AI agent** 看的：包含任务背景、当前状态、已完成的工作（含根因与证据）、
> 操作板子/发版/验证的完整方法、已知坑，以及待办清单。读完这一份即可继续工作，不需要原始开发对话。
>
> 生成时间：2026-09-17。板子：`192.168.113.161`（NanoPi M5 / RK3576）。

---

## 0. 30 秒速览

| 项 | 值 |
|---|---|
| 板子 | `192.168.113.161`，SSH 用户 `pi`（**口令不入库**：板子上的 `/home/pi/umi-handover/README.zh-CN.md` 有完整登录信息，或问设备负责人） |
| 当前发布 | `/home/pi/umi-collector/current` → `releases/0.3.3`（回滚目标 `0.2.6`） |
| 适配器版本 | `0.3.3-umi`（`recorderctl identity --json` 可查） |
| 网页控制台 | **http://192.168.113.161:8766/** |
| 采集数据根 | `/home/pi/umi-recordings/`（`incoming/` 为未封口暂存，`recordings-v2/completed/` 为已入库录制） |
| 控制器状态目录 | `/home/pi/.local/state/umi-recorder/`（jobs / deletions / recoveries / current.json / catalog.sqlite3） |
| 磁盘 | 空闲 ~53 GiB（57 GiB 总量） |
| 本机（开发机）代码 | `/home/robot/worktrees/d405-umi-rk3576`，分支 `umi-rk3576-collector-adapter` |
| 远端 | **已推送到 GitHub `sencangWei/D405-MAXIMU` 的同名分支**（2026-09-17，`554468c`） |
| 契约仓库克隆 | `/home/robot/ego-qr-audit/{ego-contracts,ego-device-platform}`（只读参考） |

**最近完成的两件事**：
1. **未完成录制（断电遗留）的网页可见 / 可删 / 可抢救** 已上线 0.3.3 并通过现场验收（用户经网页回收 46.9 GB）。
2. **QR 配网的 BLE 发现契约**在真机上端到端验证通过（广播 `EGO-ABF` + 挑战/响应 HMAC 与二维码 OOB 一致）。

**最紧要的待办**：K1 按住 → BEGIN → Wi-Fi 凭据交接这一段需要**真人按板子上的 K1 键**才能验证；验证通过后
`ego-commissioningd` 才能进发布包。

---

## 1. 任务背景

板子是 EGO 项目的 **RK3576 UMI 采集端**：D405 相机（RGB + 双 IR，H.265 720p30）+ STM32（63 字节 IMU/编码器包，400 Hz）。
它把采集数据落地到本地，供 App 侧（`JO-ara-dev/ego-recorder`）通过 `recorderctl_v1` / `preview_mjpeg_v1` /
`recording_m02_catalog_v1` / `signed_resumable_transfer_v1` / `recording_delete_v1` 合同消费。

两条工作线：
- **采集/预览/转存/删除**（主线，0.2.x → 0.3.3，已上线）
- **QR 配网**（`ego-commissioningd`，按 `JO-ara-dev/ego-contracts` 的 FROZEN 契约实现，**尚未进发布包**）

---

## 2. 已完成的工作

### 2.1 采集端主线（已发布，板上可用）

| 版本 | 内容 |
|---|---|
| 0.2.4 | 预览/采集的 D405 独占、preview handoff、编码器有界关闭、STM32 预热校验、D405 udev 电源策略 |
| 0.2.5 | 网页控制台纳入不可变发布包；采集计时单调；**录制删除流程**（Catalog 授权 + 二次确认） |
| 0.2.6 | 修 0.2.5 回归：删除后遗留 `PUBLISHED` 账本导致下次采集报 `published recording payload is unavailable`；删除时退休账本 + 恢复自愈 |
| **0.3.0** | **未完成录制的列出 / 抢救 / 删除**（见 2.2） |
| 0.3.1 | 修抢救守卫过严：码流 AU 数少于帧索引行数时直接拒绝（现场 2h06m 那段就死在这里）→ 改为取「索引与实流」较小值 + 截断索引 + `STREAM_LENGTH_MISMATCH` |
| 0.3.2 | 修抢救失败的 `.recover-<session>` 工作目录在孤儿被删后**看不见也删不掉**（现场 3.1 GB）→ 列为 `staging_leftover` 可删，删孤儿时一并回收 |
| 0.3.3 | 修**删除幂等只看 session** 导致后续残留永远删不掉；`incomplete-status` 改为返回最近一条记录 |

### 2.2 0.3.0 的核心功能：未完成录制的抢救

**问题**：断电 / 进程被杀时，原生采集器留下 `incoming/.<session>.partial/`（标记文件 `.recording` 内容 `unsealed`）。
它没有 Catalog 行，所以网页既不显示、也无法删除，磁盘被静默占用（现场曾出现 32 GB 单条 + 累计 46.9 GB）。

**方案**：
- `recorderctl incomplete-list --json` 列出所有可处理会话（尺寸、帧数、可用性探测、空间判定、是否正在写入）
- `incomplete-recover --session <id> --request-id <uuid> [--assets all|rgb|ir] [--delete-remainder] [--dry-run]`
  —— 用**自带的纯标准库 muxer** `adapter/umi_remux.py` 把原始 H.265 **无损**封装成 MP4（不重编码），
  逐资产「**验证通过才删源**」，最后作为**普通 `COMPLETE_LOCAL` 录制入库**（带 `recovery_hint` /
  `display_name`，`imu_quality_status` 如实为 `DEGRADED`，无法观测的 CRC 计数写 `null` 而不是假装 0）
- `incomplete-delete --session <id> --request-id <uuid>` 回收空间（免哈希、拒符号链接）
- 网页「未完成录制」区块：列出 + 抢救面板（三选一范围 + 实时空间提示）+ 删除二次确认

**为什么自带 muxer**：板上 GStreamer 只有 plugins-base/good + Rockchip 元素，**没有 `h265parse`**，
且 `gstreamer1.0-plugins-bad` **无安装候选**；`mp4mux`/`matroskamux` 的 H.265 sink 只接受 `hvc1`/`hev1`
（不接受 `byte-stream`），而 `mpph265enc` 输出 `byte-stream` —— 任何 GStreamer 路线都做不出可播放容器。
转码会破坏红外 y8 亮度语义（SLAM 依赖），所以选择无损字节拷贝封装。

### 2.3 QR 配网 `ego-commissioningd`（已实现，未进发布包）

按 `ego-contracts` 的 FROZEN 契约实现的 12 个模块（`rk3576/collector/commissioningd/`）：
出厂 QR 卡与身份、BlueZ legacy 广播、GATT 0002–0008、K1 物理门、X25519+HKDF+AES-GCM 凭据交接、
NetworkManager D-Bus 适配、controller 注册表 v2、本地绑定 IPC socket、systemd unit。

**已真机验证（发现契约）**：见 §6.3。**未验证**：K1 按住 → BEGIN → Wi-Fi 凭据交接（需真人按键）。

---

## 3. 环境事实与已知坑（踩过的坑，务必先读）

### 3.1 板上媒体链路
- **无 `h265parse`**，且 `gstreamer1.0-plugins-bad` 装不上 → 不要设计依赖它的方案。
- `mppvideodec`（Rockchip 硬解）的 sink caps 要求 **`parsed=true`**：解码我们产出的 MP4 时必须显式写
  `filesrc ! qtdemux ! video/x-h265,stream-format=hvc1,parsed=true,alignment=au ! mppvideodec ! ...`；
  直接把原始 Annex-B 码流喂给它会被拒（这正是采集端自己带 `h265parse` 的原因）。
- `filesink` **没有 `num-buffers` 属性**（那是源的属性）；写测试管线时别写错，否则 gst 直接拒绝整条管线。
- 解码帧数不要用「原始字节 ÷ 帧大小」估算（stride/格式会有偏差）；要精确计数用 `identity` 或对比 AU 数。

### 3.2 BLE / BlueZ（QR 配网）
- **legacy ADV_IND 上限 31 字节**：flags(3) + 128 位 service UUID(18) + 名字 `EGO-ABC`(9) = 30，
  BlueZ 默认还会加 TX Power(3) → 超限被控制器拒绝（现象：`Failed to add UUID: Authentication Failed (0x05)`，
  空口上只有 UUID、**没有名字**）。必须设 `IncludeTxPower=false`。
- **注册必须异步 + 独立线程**：在 GLib 主循环回调里做**同步阻塞**的 `RegisterApplication`，主循环就无法应答
  BlueZ 的回调查询，BlueZ 报 `gatt-database.c:client_ready_cb() No object received`（D-Bus 侧是 `NoReply`）。
- **BlueZ 在连接建立后会停止广播，断开后不一定自动恢复** → 验证脚本跑完要重启守护进程重新注册。
- **多个守护进程实例会各占一个广播实例**（`ActiveInstances>1`），BlueZ 会在实例间轮转，表现为「广播时有时无」。
  调试前务必 `pkill -f "app/src/main.py"` 并 `systemctl restart bluetooth` 清干净。
- 客户端读 GATT 特征要用 **`ReadValue({})` 方法**，不要读 `Properties.Value`（那是缓存，通常为空）。
- 实验室里还有别的 EGO 设备（RDK X5，广播名 `EGO-501`，`device_x5_01`）会干扰扫描匹配 ——
  **按地址或精确名字匹配**，不要只按 service UUID（BlueZ 会把 UUID 合并进无关设备的缓存条目）。

### 3.3 采集端行为
- 相机偶发 `librealsense: Frame did not arrive in time!`（SIGKILL 采集后见过两次），相机随后**能自行恢复**；
  必要时重启 `umi-preview.service` 重新初始化 D405，或重新插拔 USB。
- 短于 ~60 秒的采集可能因 STM32 速率窗口（395–405 Hz）判据不稳而失败；验收用 ≥60 秒。
- `incomplete-status --session` 的投影**不含 `output_dir`**，脚本里用
  `recordings-v2/completed/<recording_id>` 推导。
- 用 `glob` 列举 `incoming/` 会漏掉点开头的目录（`glob` 默认不匹配隐藏项）——用 `os.listdir` 或 `ls -a`。

---

## 4. 代码与产物位置

### 4.1 上位机（开发机）
| 路径 | 说明 |
|---|---|
| `/home/robot/worktrees/d405-umi-rk3576` | 源码 worktree，分支 `umi-rk3576-collector-adapter`，**11 个提交未 push** |
| `rk3576/collector/adapter/umi_recorderctl.py` | 控制器 CLI（identity/preflight/start/stop/status/logs/delete-\*/incomplete-\*） |
| `rk3576/collector/adapter/umi_publish.py` | 封口→账本→MANIFEST→Catalog 发布流程 |
| `rk3576/collector/adapter/umi_remux.py` | **纯标准库 Annex-B H.265 → MP4(hvc1) 无损封装器** |
| `rk3576/collector/web-console/` | 网页控制台（`server.py` 库 + `device_server.py` 入口 + `remote_agent.py` 桥 + `static/`） |
| `rk3576/collector/commissioningd/` | QR 配网守护进程（未进发布包） |
| `rk3576/collector/DEPLOYMENT_AND_USAGE.zh-CN.md` | 运维文档（安装/配置/验收/回滚/排障，含 0.3.x 逐版本说明） |
| `rk3576/collector/docs/QR_COMMISSIONING_AUDIT_AND_DESIGN.zh-CN.md` | QR 配网契约审计与实施方案 |
| `/home/robot/ego-qr-audit/` | `JO-ara-dev/ego-contracts` 与 `ego-device-platform` 的只读克隆（契约真源） |

### 4.2 本仓库工具（本目录 `tools/`，均为上位机脚本）
| 文件 | 用途 |
|---|---|
| `tools/board_run.py` | 用 pexpect 密码登录把本地脚本推到板子并执行（最常用的板上操作方式） |
| `tools/ego-ble-central.py` | **本机当手机端**的 BLE central：扫描/连接/读身份/挑战-响应 HMAC 校验 |
| `tools/rk3576-release-build.sh` | 构建不可变发布包（基线覆盖 → manifest → SHA256SUMS → preflight → tar） |
| `tools/rk3576-release-deploy.py` | 部署到板子（校验 sha256 → install.sh 原子切换 → 重启服务 → 可选跑验证脚本） |

### 4.3 板子上
| 路径 | 说明 |
|---|---|
| `/home/pi/umi-collector/` | 发布根：`releases/<version>/` + `current` 符号链接（install.sh 原子切换） |
| `/home/pi/.config/umi-recorder/umi-recorder.env` | 设备环境（UMI_DEVICE_ID / 序列号 / 串口 / 目录），**改配置只改这里** |
| `/home/pi/.config/umi-recorder/tls/` | 管理服务证书 |
| `/home/pi/umi-recordings/` | 数据根（见 §0） |
| `/tmp/commd-live/` | **测试用**的 commissioningd 暂存区（身份的 OOB 密钥等；重启即清，非生产路径） |

---

## 5. 常用操作

### 5.1 操作板子
```bash
# 最常用：把本地脚本推到板上执行（自动处理密码）
python3 tools/board_run.py /path/to/local_script.sh

# 手工：ssh pi@192.168.113.161    # 口令见板子上的交接文档（仓库版不含口令）
```
板上所有 `recorderctl` 调用都需要先加载环境：
```bash
set -a; . /home/pi/.config/umi-recorder/umi-recorder.env; set +a
RC=/home/pi/.local/bin/recorderctl
$RC identity --json | python3 -m json.tool
$RC preflight --json
$RC status --json                       # 当前/最近一次采集
$RC start --json --request-id $(cat /proc/sys/kernel/random/uuid) --duration 20
$RC stop  --json --job-id <job>
$RC delete-recording --json --recording-id <rid> --request-id $(cat /proc/sys/kernel/random/uuid)
$RC delete-status --json [--recording-id <rid>]
$RC incomplete-list --json
$RC incomplete-recover --session <id> --request-id <uuid> [--assets ir] [--dry-run]
$RC incomplete-delete  --session <id> --request-id <uuid>
$RC incomplete-status  [--session <id>] --json
```
网页：`http://192.168.113.161:8766/`（服务：`systemctl --user status umi-preview umi-admin umi-web`）。

### 5.2 发版（不可变包）
```bash
# 1) 先在源码里把版本号改掉（三处必须一致）
#    adapter/umi_recorderctl.py: CONTROLLER_VERSION
#    tests/test_rk3576_umi_collector_adapter.py: 版本断言
#    rk3576/collector/README.md + DEPLOYMENT_AND_USAGE.zh-CN.md: 版本说明段
bash tools/rk3576-release-build.sh 0.3.4          # 产出 /home/robot/rk3576-deploy-0.3.4-build/rk3576-umi-0.3.4.tar.gz

# 2) 跑测试（ROS Humble 的 launch_testing 插件会破坏收集，必须禁插件自动加载）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_rk3576_*.py -q -p no:cacheprovider

# 3) 部署（会做板端 sha256 校验、install.sh 原子切换、重启三个用户服务）
python3 tools/rk3576-release-deploy.py 0.3.4 [--verify-script /tmp/verify.py]
```
- 打包配方要点：基线取 `0.2.3-fix1` 树（在 `/home/robot/rk3576-deploy-0.2.6-ddi8s1ck/rk3576-umi-0.2.6`），
  覆盖 `adapter/`、`web-console/`、`check-host.sh`、`README.md`、`DEPLOYMENT_AND_USAGE.zh-CN.md`；
  `release-manifest.json` 的 `files[]` **必须排除它自己和 `SHA256SUMS`**；`SHA256SUMS` 在目录**外**生成再移入。
- 回滚：`ln -sfn releases/0.2.6 /home/pi/umi-collector/.current && mv -Tf .../current`（见运维文档 §9），
  回滚后记得 `systemctl --user restart umi-preview umi-admin umi-web`。

### 5.3 验证（回归）
```bash
# 单元测试（本机）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_rk3576_umi_remux.py \
  tests/test_rk3576_umi_incomplete.py tests/test_rk3576_umi_collector_adapter.py \
  tests/test_rk3576_web_console.py tests/test_rk3576_commissioningd_*.py -q -p no:cacheprovider
# 板端运行时自检（安装后应打印 HOST_RUNTIME_PASS）
ssh pi@192.168.113.161 'bash /home/pi/umi-collector/current/check-host.sh'
```

---

## 6. 验收证据（都是真机实测，可复现）

### 6.1 未完成录制闭环
- 现场 4 个孤儿被网页列出（32.5 GB / 14.4 GB / 38 MB / 1 MB，帧数与尺寸正确），用户经网页删除后
  **磁盘 47 G → 4.1 G**，`incoming/` 清空；另有一个 3.1 GB 残留工作目录被 `incomplete-delete` 回收。
- 自造孤儿（采集 → SIGKILL 原生进程 → 列出 → 抢救 → 入库 → 解码 → 走**现有**删除路径 → 紧接可再采集）全链路通过。
- **无损性**：封装出的 MP4 里 AU 数与原生采集器自己记录的帧数**逐路精确一致（659/659）**；
  GStreamer 自己的 `qtdemux` 导出的 NAL 与源码流逐字节相同。
- **可解码**：`mppvideodec` 硬解三个 MP4 零错误（MPP 日志 `h265d: extradata is encoded as hvcC format`）。
- 入库真实性：`state=COMPLETE_LOCAL`、`save_state=LOCAL_ONLY`、`display_name=…已抢救（…）`、`recovery_hint` 带
  `RECOVERED_PARTIAL selection=… warnings=…`、`imu_quality_status=DEGRADED`、`stm32.crc_errors=null` 且
  `crc_counters_observable=false`。
- 空间判定：32 GB 那段全救需 16.72 GiB > 当时 7 GiB 空闲 → 正确拒绝并报出所需/可用；
  14 GB 那段仅红外 3.60 GiB → 判定可做。

### 6.2 采集主链路
- 20 秒采集 `complete_local`（600 对 / 8000 IMU / exit 0）；删除后紧接可再采集（0.2.6 回归检查）。

### 6.3 BLE 发现契约（QR 配网）
```
空口广播:  50:31:23:0E:CD:45  EGO-ABF
           uuid f3e0f8d0-7a11-4c9e-9d4b-45474f4f0001   ← 仅契约固定 UUID
           service_data: (omitted)                      ← 契约要求不带
连接 → 服务解析 → 0002..0008 特征（读/写/通知权限与契约一致）
DeviceIdentity: {"device_id":"device_rk3576_161","product_model":"EGO-RK3576","software_version":"0.3.3"}
写入 24 字节挑战 → 读回 32 字节 HMAC 证明
HMAC proof matches the QR OOB secret: True
```
复现：`sudo systemctl restart bluetooth` 后启动守护进程（见 §7.2），然后
```bash
BOARD_ADDRESS="50:31:23:0E:CD:45" SCAN_SECONDS=20 OOB_SECRET="<板上二维码 OOB>" \
  python3 tools/ego-ble-central.py
```
> 同一个脚本也能配通实验室里的 RDK X5 设备（`device_x5_01` / `0.8.0-x5`），说明契约可互操作。

---

## 7. 待办（按优先级）

### 7.1 需要真人配合：K1 → Wi-Fi 配网全流程
必须**有人按住板子上的 K1 键 ≥5 秒**（契约强制物理门，不可替代）。步骤：
1. `sudo systemctl restart bluetooth`，然后启动守护进程（§7.2 的 systemd-run 命令）
2. 用 central 脚本连接并按 §5.3 校验身份
3. **真人按住 K1 五秒**（守护进程日志出现 `K1 commissioning hold observed`）
4. central 侧发 BEGIN（X25519 client 公钥 + controller P-256 公钥 + OOB proof），经 GATT `...0005` 写入
5. 经 `...0007` 写 AES-GCM 加密的 `{ssid,passphrase,security_mode}` → 板子经 NetworkManager 应用 → 轮询 `...0008` 直到 `CONNECTED`（含 `https_origin` + 证书指纹）
6. 完成后：`ActiveInstances` 检查、controller 注册表 `/etc/ego/ble/controller-bindings.json` 检查

> BEGIN 的字段名、OOB proof 域名、IPC protocol 常量是**实现侧的解释项**（契约只有散文），
> 见 `commissioningd/README.md`；若对方 App 的实现不同，需要对齐。

### 7.2 把 commissioningd 纳入发布包（K1 验证通过后）
当前它不在 tarball 里。纳入时需要：
- `install.sh` 的 `chmod 755` 列表加 `commissioningd/main.py`
- `service/ego-commissioningd.service`（systemd system unit，注意 unit 里写的是 `/opt/umi-collector/...`，
  与实际安装根 `/home/pi/umi-collector` **不一致，必须统一**）
- 生产身份/证书路径：`/etc/ego/ble/device-identity.json`（`qr_factory.py provision` 生成）、`/etc/ego/tls/device-api.pem`
- `check-host.sh` 增加 `python3 -c 'import dbus, gi'` 与 `bluetoothctl`/`NetworkManager` 存在性检查
- 出厂流程：`python3 commissioningd/qr_factory.py provision --device-id … --qr-out … --png-out …` 生成密封二维码卡

启动（临时验证用，不改系统）：
```bash
sudo systemd-run --unit=ego-commd --collect --property=Type=simple \
  --working-directory=/tmp/commd-live/app/src /usr/bin/python3 main.py \
  --identity-path /tmp/commd-live/device-identity.json \
  --registry-path /tmp/commd-live/controller-bindings.json \
  --ipc-path /tmp/commd-live/ble-binding-v1.sock \
  --https-origin https://192.168.113.161:18443 \
  --cert-path /tmp/commd-live/device-api.pem
```

### 7.3 其他
- **长时 HIL**：5–10 分钟实时采集验收（现有验收都是 20–60 秒）。
- **PR（可选）**：11 个提交已于 2026-09-17 推送到 `sencangWei/D405-MAXIMU` 的
  `umi-rk3576-collector-adapter` 分支（远端 `sencang`）；如需 PR 再开。
  相关远端仓库：`JO-ara-dev/ego-recorder`（App 侧）、`JO-ara-dev/ego-contracts`、`JO-ara-dev/ego-device-platform`。
  注意：访问 `JO-ara-dev` 私有仓需要交叉审核人账号 `aaaaaaxwb`（当时的 OAuth token 在 `/tmp/gh-qqconfig`，
  **可能已过期**）。本机 `gh` 默认登录的是 `sencangWei`。
- **契约缺口上报**：`ego-contracts` **没有任何 Wi-Fi 漫游 / 重绑 / 换 IP 后的身份恢复契约**
  （mDNS/SoftAP 被明确排除），设备换网后 App 没有契约定义的再发现路径 → 需要 JO-ara-dev 增补或明确「重配网=重走完整流程」。
- **G-001（0.4.0 范围）**：TLS 1.3 Device API、bearer ticket 签发/刷新/吊销、`/physical-confirmations` 路由，
  等 `ego-recorder` 明确消费侧后再做。
- 3.1 GB 那段 31.6 分钟的正式录制（…ce4bd567）在 0.2.5 时代被网页删除且未转存，**不可恢复**（历史事实，供追溯）。

---

## 8. 关键文件索引（排查问题时先看这些）

| 内容 | 位置（板上） |
|---|---|
| 当前/最近采集任务 | `/home/pi/.local/state/umi-recorder/current.json`、`jobs/umi-*.json` |
| 采集器原始日志 | `jobs/umi-*.collector.log` |
| 删除操作记录 | `state_dir/deletions/recording_*.json` |
| 抢救操作记录 | `state_dir/recoveries/<session>.json` |
| 未完成录制删除记录 | `state_dir/incomplete-deletions/<session>.json` |
| 录制目录库 | `state_dir/catalog.sqlite3`（表 `recordings` / `catalog_recording_assets`） |
| 发布账本 | `umi-recordings/recordings-v2/.publication-ledger/<recording_id>.json` |
| 服务日志 | `journalctl --user -u umi-preview -u umi-admin -u umi-web` |
| 守护进程日志 | `journalctl -u ego-commd*`（临时单元）；`journalctl -u bluetooth` |

---

## 9. 交接检查清单

- [ ] 能 SSH 上板（`pi`/`pi`），`recorderctl identity` 显示 `0.3.3-umi`
- [ ] 打开 http://192.168.113.161:8766/ 能看到「未完成录制」区块
- [ ] 跑一遍单元测试（§5.3），全绿
- [ ] 跑一次 20 秒采集并删除，确认无异常
- [ ] 读 §3「已知坑」再动手
- [ ] 若要继续 QR 配网：先约人按 K1，再按 §7.1 走
