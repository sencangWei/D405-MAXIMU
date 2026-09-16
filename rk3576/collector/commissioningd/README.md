# ego-commissioningd

EGO BLE Wi-Fi 配网守护进程（RK3576 采集端 0.3.0 新增组件）。

## 契约来源

实现以下 FROZEN / V0.8-BASELINE.3 契约（JO-ara-dev/ego-contracts@829c8d8）：

- `internal/ble-commissioning-qr-v1` —— 出厂 QR 卡 payload（schema 强校验）
- `internal/ble-discovery-v1` + `ble-discovery-legacy-profile-v1` —— 唯一 legacy
  `ADV_IND` 广播（EGO-ABC + 固定 service UUID）与 challenge/HMAC 绑定证明
- `internal/ble-wifi-commissioning-v1` —— K1 物理门（≥5s → 120s 窗口）、
  12 字节分帧、X25519+HKDF 会话密钥、AES-256-GCM 凭据信封、NetworkManager
  system D-Bus 凭据交接
- `internal/device-auth-controller-lifecycle-v2` —— ≤8 条不透明 controller 注册表
- `internal/device-auth-network-ready-v1` —— ...0008 状态对象
- `internal/device-auth-controller-binding-ipc-v1` —— 本地绑定 IPC socket

## 模块

| 文件 | 职责 |
|---|---|
| `crypto_glue.py` | 分帧头/重组、HMAC 证明、X25519/HKDF/AES-GCM（金标向量锁定） |
| `identity.py` | 出厂身份（device_id/discriminator/OOB/X25519），QR payload |
| `registry.py` | `/etc/ego/ble/controller-bindings.json` v2 注册表 |
| `k1_trigger.py` | K1 evdev 观察（udev ID_PATH + DT label + key 0x101 解析） |
| `commissioning.py` | 配网会话状态机（K1 门 → BEGIN → 凭据 → 网络交接） |
| `network.py` | NetworkManager D-Bus 凭据应用（仅内存传输，失败清 profile） |
| `advertiser.py` | BlueZ legacy ADV_IND 注册（唯一属主） |
| `ble_gatt.py` | GATT 应用（...0002–...0008） |
| `ipc_sock.py` | `/run/ego/ble-binding-v1.sock` SOCK_SEQPACKET 网关 |
| `qr_factory.py` | 出厂工具：生成身份 + QR payload（+ 可选 PNG） |
| `main.py` | 守护进程入口（GATT 先注册，广告后注册） |

## 已知解释项（契约未机器冻结，实现侧已钉死并测试）

1. BEGIN 明文 JSON 字段名与 `protocol=EGO_BLE_WIFI_BEGIN_V1`；
   `session_id` 为 32 位小写十六进制。
2. BEGIN OOB proof 构造：
   `HMAC-SHA256(oob, "EGO_BLE_COMMISSIONING_PROOF_V1\0" || device_id || session_id || client_public_key)`。
3. IPC `protocol=EGO_DEVICE_AUTH_BINDING_IPC_V1`。
4. 无 Manager 的本期部署中，IPC socket 由 commissioningd 代管（协议格式不变）。

## 运行要求

- BlueZ（hci0）+ NetworkManager（wlan0）已运行
- `python3-dbus`、`python3-gi`、`python3-cryptography`
- 出厂身份：`qr_factory.py provision ...`（root，0600）
- K1 键：DT label `K1`、key code `0x101`、udev `ID_PATH=platform-gpio-keys`

## 安全边界

- OOB 32 字节：不进广告/日志/IPC；只存 `/etc/ego/ble/device-identity.json`（0600）。
- Wi-Fi 凭据：解密后仅经 NetworkManager D-Bus 传入；不落盘、不进日志、不用 shell。
- 一切对外错误经消毒（稳定错误码，不区分资源是否存在）。
- 金标向量：`tests/fixtures/ble-wifi-commissioning-v1/crypto-vector.json`。
