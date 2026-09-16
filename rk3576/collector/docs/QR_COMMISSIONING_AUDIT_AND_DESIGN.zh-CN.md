# QR 配网契约审计与 RK3576 采集端实施方案

日期：2026-09-16
状态：契约审计完成（FROZEN 基线），板上能力核查通过，待开工实现
权限来源：aaaaaaxwb 账号（JO-ara-dev 交叉审核人，WRITE 权限）

## 1. 契约来源（已克隆到本机）

| 仓库 | commit | 说明 |
|---|---|---|
| JO-ara-dev/ego-contracts | 829c8d8ec11b | 契约真源，internal/ 为本次审计对象 |
| JO-ara-dev/ego-device-platform | 5784ac73b2e | EGO V0.8 平台（Go Manager + C++ Acquisition），尚无 QR/BLE 实现 |

克隆位置：`/home/robot/ego-qr-audit/`（只读用途）。

## 2. 契约链全景（全部为 FROZEN / V0.8-BASELINE.3）

### 2.1 出厂 QR 卡（ble-commissioning-qr-v1，FROZEN）
- QR 内容 = 单个 canonical UTF-8 JSON，schema 强制：
  - `protocol` = `EGO_BLE_COMMISSIONING_QR_V1`
  - `service_uuid` = `f3e0f8d0-7a11-4c9e-9d4b-45474f4f0001`（固定）
  - `device_id`：不透明持久身份，`^[A-Za-z0-9._-]{1,256}$`
  - `discriminator`：3 位十六进制（非秘密，广告名 EGO-ABC 的 ABC）
  - `oob_secret`：正好 32 字节，unpadded base64url（43 字符）——**授权秘密**
  - 可选 `device_public_key_sha256`（小写 64 hex，X25519 公钥指纹）
- QR 卡永不含 Wi-Fi 密码、IP、bearer、私钥、BT MAC、BlueZ 路径。
- OOB 不得进日志/广告/Manager IPC，只有授权 commissioning central 与受保护
  commissioning daemon 持有。

### 2.2 BLE 发现与绑定证明（ble-discovery-v1 + ble-discovery-legacy-profile-v1）
- 唯一属主进程 `ego-commissioningd`；唯一 connectable legacy `ADV_IND`（LE 1M），
  只带 general-discoverable flags + 128-bit service UUID + 短名 `EGO-ABC`；
  无 service data、无 IP/MAC/完整 device_id。
- GATT（UUID 基地址 f3e0f8d0-7a11-4c9e-9d4b-45474f4f00XX）：
  - `...0002` DeviceIdentity（READ）：`{device_id, product_model, software_version}`
  - `...0003` BindingChallenge（WRITE）：恰好 16–32 字节新随机数；新 challenge 作废旧的
  - `...0004` BindingResponse（READ/NOTIFY）：`HMAC-SHA256(key=oob_secret,
    msg="EGO_BLE_BINDING_PROOF_V1\0" || device_id || challenge)`，32 字节
- challenge 未写前不得暴露 response；证明不是授权令牌。
- 历史 C advertiser 不得与 commissioningd 并行；注册失败 → 发现不可用，fail closed，
  不允许降级到备用 profile。

### 2.3 Wi-Fi 配网（ble-wifi-commissioning-v1，物理门 + 加密凭据）
- **物理门**：用户按住 K1 ≥5 秒 → 打开 120 秒 commissioning 窗口；同时只允许一个会话；
  K1 不可被布尔值/HTTP/注入事件替代。
- **首绑 BEGIN**：client 生成 16–32 字节 challenge + P-256 签名公钥；BEGIN 携带
  device_id、discriminator、session_id、X25519 client 公钥、controller 公钥、QR OOB proof。
  设备校验 proof + 物理窗口后生成不透明 controller_id（调用方不可自选）。
- **分帧**：GATT write 固定 12 字节小端头（version=1, message∈{5=Control,7=Credentials},
  flags=0, reserved=0, transport_session u32 非零, sequence u16 非零严格递增,
  fragment_index, fragment_count 1..32）；ATT payload ≤256B、单分片数据 ≤244B、
  消息 ≤4096B。重传已接受分片幂等；改字节/跳号/坏 UTF-8/尾部 JSON → fail closed。
- **加密**：X25519 + HKDF-SHA256（salt=`EGO_BLE_WIFI_COMMISSIONING_V1\0`，
  info=`EGO_BLE_WIFI_SESSION_KEY_V1\0 || device_id\0controller_id\0session_id`）→
  32 字节会话密钥；AES-256-GCM，12 字节 nonce；AAD = 12 字节逻辑头
  （fragment_index=0）+ `device_id\0controller_id\0session_id`。明文为严格 JSON，
  只含 `ssid`/`passphrase`/`security_mode`（OPEN/WPA2_PSK/WPA3_SAE）。
- **网络交接**：仅 NetworkManager system D-Bus 适配器在内存中收到解密凭据；
  120 秒 deadline 内报 APPLYING/CONNECTED/消毒失败并清理失败 profile；
  不用 shell 命令、不落地临时文件。
- DHCP 稳定后通过加密 status 特征只回 `https_origin` + 小写叶子证书 SHA-256；
  永不回退 0.0.0.0/HTTP/mDNS/SoftAP。
- 会话单次使用、120 秒过期；断连/重启/challenge 替换/取消/身份变化清会话与密钥；
  已完成的 controller 绑定在 BLE 断开后仍然存活。
- **金标向量**：`tests/fixtures/ble-wifi-commissioning-v1/crypto-vector.json` 锁定
  X25519/HKDF/头/AAD/nonce/AES-GCM 字节（本实现必须逐字节复现通过）。

### 2.4 绑定注册 IPC 与 controller 注册表（v2 权威）
- `device-auth-controller-binding-ipc-v1`：QR/OOB + K1 门成功后，commissioningd 经
  `/run/ego/ble-binding-v1.sock`（Unix SOCK_SEQPACKET，Manager 创建，SO_PEERCRED，
  仅批准 UID/GID）发 `REGISTER`（protocol/event/event_id/device_id/device_identity/
  P-256 controller_public_key，base64url 未压缩点）；Manager 生成不透明 controller_id
  + 原子写注册表 + 返回一次性 `bootstrap_reference`（32 字节随机，≤120 秒）。
  `DISCONNECT` 只结束会话，不吊销已完成的绑定。event_id 单次使用。
- `device-auth-controller-lifecycle-v2`：root 拥有 `/etc/ego/ble/controller-bindings.json`，
  ≤8 条（controller_id + P-256 公钥 + 过期/吊销态）；不持久 MAC/BlueZ 路径/IP；
  解析失败/重复/过期/超 8 条 → fail closed。物理 reset 吊销全部绑定。
- `device-auth-binding-ipc-v1` 仅为旧发现 fixture 的兼容通道，基线不再用它注册新 controller。

### 2.5 G-001 bootstrap 与 Device API 认证（device-auth-bootstrap-v1，COMM-CANDIDATE/未发布）
- BLE 验证后产出 `EGO_DEVICE_BOOTSTRAP_V1` 记录：device_id、controller_id、
  https_origin（绝对 https，禁明文）、server_certificate_sha256（64 小写 hex）、
  bearer_ticket（16–4096 字符）、issued_at/expires_at（≤15 分钟）。
- TLS 监听器 `POST /api/v1/device-auth/bootstrap`：TLS1.3 + 客户端 pin 服务器证书 +
  一次性 handoff_reference（绑定 exact device/controller/瞬时 BlueZ 身份，恰好消费一次）。
- 之后所有 Device API 请求带 `Authorization: Bearer <ticket>`；401 不区分设备/
  controller/证书/ticket 是否存在。base path 固定 `/api/v1`。
- `device-auth-refresh-v1`：refresh 用当前 ticket + 16–64 字节 nonce 以注册 P-256 私钥签名
  （`EGO_DEVICE_AUTH_REFRESH_V1\0 || device_id || 0x00 || controller_id || 0x00 || nonce`），
  新 ticket ≤15 分钟；nonce 单次使用。
- `device-auth-network-ready-v1`：...0008 状态对象的字段规则（CONNECTED 必含
  https_origin + server_certificate_sha256；WAITING/APPLYING/FAILED 语义）。

### 2.6 物理确认（physical-trigger/physical-confirmation-issuance）
- K1 是双用途物理源：短按 = Export 确认（SHORT_PRESS）；≥5 秒 = 配网门（COMMISSIONING_HOLD）。
  同一 evdev 观察者串行化两类事件，两进程不得竞争设备。
- 触发源解析规则：udev `ID_PATH=platform-gpio-keys` + DT label `K1` + key code `0x101`，
  禁止硬编码 `/dev/input/eventN`。
- `/api/v1/physical-confirmations`（G-001 保护）：只收完整 binding，等待一次 K1 或已绑定
  BLE 触发，返回不透明 reference（120 秒单次使用）。

## 3. 契约缺口（需在 JO-ara-dev 侧澄清，不在本实现范围）

1. **Wi-Fi 漫游/身份恢复无任何契约**：全仓无 roam/rebind 语义。设备换 SSID、DHCP
   换 IP、https_origin 变化后，App 无契约定义的再发现路径（mDNS/SoftAP 被明确排除，
   网络就绪状态只在 commissioning 会话内单发）。→ 需要 JO-ara-dev 增补
   "roaming identity recovery" 契约（或明确重配网 = 重新走完整 QR+K1 流程）。
2. **首绑之后的"重绑"**：契约只允许 ≤8 个并存 controller；每个新 controller 都走同一
   QR+OOB+K1 门。没有"同 controller 换机重绑"或"恢复出厂后保留"语义（物理 reset =
   全吊销）。旧机摘除需经认证的 DELETE /controllers/{id}。
3. **ego-recorder App 现用 recorderctl_v1 与 G-001 的关系**：bootstrap/ticket 体系属于
   EGO V0.8 Device API 线；采集端 0.2.x 的 Web 控制台是另一套认证面。QR 配网实现可
   独立交付（BLE+Wi-Fi 上网络），G-001 TLS/bearer 接入需 ego-recorder 明确消费侧后
   再做。

## 4. 板上能力核查（192.168.113.161，2026-09-16）

| 要求 | 实测 | 结论 |
|---|---|---|
| BLE 适配器 + BlueZ | hci0（UART），BlueZ 5.72 active，rfkill 无阻塞 | ✅ |
| NetworkManager system D-Bus | NM 1.46 active，wlan0 已连 @Ruijie-s6145 | ✅ |
| K1 evdev | `/dev/input/event5` = gpio-keys，udev `ID_PATH=platform-gpio-keys`；DT button@1 label=`K1`、`linux,code=0x00000101`；EVIOCGBIT 恰好支持 KEY 0x101 | ✅ 与契约解析规则完全吻合 |
| Python 加密 | cryptography 41.0.7（X25519/HKDF/AESGCM 齐全） | ✅ |
| Python D-Bus | dbus-python 可用；无 dasbus/pydbus（可 pip 装 dasbus 或直接用 dbus-python） | ✅（用 dbus-python） |

## 5. 实施方案：采集端 `ego-commissioningd`（Python，新组件）

命名沿用契约属主名 `ego-commissioningd`，作为 0.3.0 的独立守护进程加入发布包，
与采集主进程解耦（契约要求单一 BlueZ/GATT 属主；采集面不碰 BLE）。

### 5.1 组件划分

```
ego-commissioningd/
  identity.py        # 出厂身份：device_id/discriminator/oob_secret/X25519 密钥对
                     #   持久化 /etc/ego/ble/device-identity.json（root, 0600）
                     #   OOB 只存内存 + 该文件；日志/IPC 永不出现
  qr_factory.py      # 出厂工具：生成 QR payload JSON（schema 校验）+ QR 卡 PNG
  ble_gatt.py        # BlueZ GATT application（dbus-python，org.bluez.GattManager1）
                     #   ...0002 Identity / ...0003 Challenge / ...0004 Response
                     #   ...0005 Control / ...0006 DeviceKey / ...0007 Credentials / ...0008 Status
  advertiser.py      # LEAdvertisingManager1 注册唯一 legacy ADV_IND
                     #   flags=general-discoverable, UUID=...0001, name=EGO-<disc>
  k1_trigger.py      # evdev 监听（udev 按 ID_PATH+label+code 解析，禁硬编码 eventN）
                     #   短按 → Export 确认事件；≥5s 连续按住 → COMMISSIONING_HOLD
                     #   防抖；同一观察者串行化两类事件
  commissioning.py   # 会话状态机：challenge → OOB HMAC 校验 → K1 窗口(120s) →
                     #   BEGIN 分帧重组 → X25519+HKDF 会话密钥 → AES-GCM 解密凭据
                     #   → NetworkManager D-Bus 应用 → DHCP 轮询 → ...0008 状态
  crypto_glue.py     # X25519/HKDF/AES-GCM + 12 字节头编解码 + 严格 JSON 校验
  registry.py        # /etc/ego/ble/controller-bindings.json（v2，≤8 条，原子写）
  ipc_sock.py        # /run/ego/ble-binding-v1.sock（SOCK_SEQPACKET，SO_PEERCRED）
                     #   REGISTER/DISCONNECT；bootstrap_reference 生成（≤120s，单次消费）
  main.py            # 守护主循环 + systemd unit
```

### 5.2 关键实现要点

1. **金标向量先行**：`crypto_glue.py` 开发第一步用
   `ego-contracts/tests/fixtures/ble-wifi-commissioning-v1/crypto-vector.json`
   做单元测试，shared_secret/session_key/AAD/ciphertext 逐字节比对，不过不做后续。
2. **fail-closed 矩阵**：重放分片幂等接受；改字节/跳号/坏 UTF-8/尾 JSON/未知字段/
   过期窗口/无 K1 门 → 拒绝并清会话。所有对外错误经消毒（不区分设备/controller/
   证书/ticket 存在性）。
3. **OOB 卫生**：不出现在广告、日志、Manager IPC、文件系统投递箱；仅内存持有
   （identity 文件单独 root 0600，daemon 启动时读入后不再回写）。
4. **凭据卫生**：解密后仅经 NetworkManager D-Bus AddAndActivateConnection2 传入，
   不落盘临时文件、不进日志；失败 profile 清除；120s deadline。
5. **首绑门**：没有 K1 ≥5s 的窗口，BEGIN 直接拒；窗口单次使用；多 controller 绑定
   各自走完整门（与契约"重绑=完整流程"一致）。
6. **IPC 对接现状**：采集端当前无 Go Manager。本期 REGISTER 的接收方先落到
   commissioningd 自带的 registry.py（契约 v2 语义：不透明 ID、原子写、≤8 条），
   socket 接口按契约冻结格式实现，后续 Manager 合入时原样对接。
7. **与采集面共存**：commissioningd 独立于 recorder/web-console 进程；Wi-Fi 凭据
   应用可能影响预览/转存网络路径，部署文档需说明配网期间勿进行中的转存任务。

### 5.3 分期

- **0.3.0（本次）**：identity/qr_factory + advertiser + GATT 0002–0004 +
  k1_trigger + crypto_glue（金标向量过）+ 0005–0008 + NetworkManager 交接 +
  registry + IPC socket + systemd unit + 单测（含向量测试、fail-closed 矩阵）。
- **0.4.0（待 ego-recorder 明确消费侧）**：G-001 TLS Device API、bootstrap 路由、
  bearer ticket 签发/刷新/吊销、/physical-confirmations 路由。
- **上游澄清项**：§3 的漫游身份恢复契约缺口，提交 JO-ara-dev 讨论。

### 5.4 验收

1. 金标向量单测逐字节 PASS。
2. 手机（或 BlueZ central 仿真）扫 QR → 连接 → Identity 校验 → challenge/response
   HMAC 验证 → K1 按住 5s → 发 BEGIN + WPA2 凭据 → 板上 wlan0 切换 → ...0008
   CONNECTED 带 https_origin + 证书指纹。
3. fail-closed 矩阵用例全过（重放/跳号/无门/过期/改字节）。
4. 无 K1 门时 BEGIN 必拒；窗口 120s 过期必拒。
5. OOB/凭据/私钥在日志与抓包中零出现（验收时 grep 审计）。
6. 采集主进程在配网全程不受影响（并发 smoke）。

## 6. 安全与权限收尾提醒

- 本次审计用 aaaaaaxwb 账号（浏览器 OAuth，token 在 /tmp/gh-qqconfig，用完即删）。
- 用户应在浏览器退出 1938504128@qq.com 并在 GitHub Settings → Applications
  撤销 "github" OAuth 授权。
