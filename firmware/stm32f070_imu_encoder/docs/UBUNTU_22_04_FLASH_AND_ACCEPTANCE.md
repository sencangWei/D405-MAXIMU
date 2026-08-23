# Ubuntu 22.04 烧录与到板验收

适用硬件：STM32F070F6P6 联合采集板、KT-EX9、AS5047P、CP2102N 和 ST-Link。本文使用已经选定的 Mode B：确认 `EB 90 22` 帧头后立即读取编码器，完整 IMU 帧校验成功后再组成 63 字节联合包。

## 1. 安装环境

```bash
sudo apt update
sudo apt install -y build-essential curl git python3 python3-pip python3-venv udev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install platformio pyserial
pio --version
```

`build-essential` 提供原生 C++ 单元测试需要的 `gcc/g++`，不能省略。

安装 PlatformIO 官方 udev 规则：

```bash
curl -fsSL https://raw.githubusercontent.com/platformio/platformio-core/develop/platformio/assets/system/99-platformio-udev.rules \
  | sudo tee /etc/udev/rules.d/99-platformio-udev.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -a -G dialout "$USER"
```

执行后注销并重新登录，然后拔插 ST-Link 和采集板。检查：

```bash
id
lsusb
pio device list
```

## 2. 软件测试与编译

进入含有 `platformio.ini` 的目录：

```bash
cd firmware/stm32f070_imu_encoder
python -m unittest discover -s tools -p 'test_*.py' -v
pio test -e native
pio run -e stm32f070f6p6
sha256sum .pio/build/stm32f070f6p6/firmware.bin
(cd release && sha256sum -c SHA256SUMS)
```

判定标准：Python测试和原生C++测试全部通过；STM32构建无警告；最后一条显示 `firmware.bin: OK`。仓库固定使用 `ststm32@19.7.1`，首次构建会自动下载工具链。

## 3. ST-Link 接线

必须按信号名连接，不要凭排针位置猜顺序：

| ST-Link 信号 | 板上 J4 信号 |
|---|---|
| GND | GND |
| SWDIO | SWDIO / PA13 |
| SWCLK | SWCLK / PA14 |
| NRST | NRST |
| VAPP/TVCC（目标电压检测） | 3V3 |

推荐让采集板由自己的 USB-C 正常供电，并让 ST-Link 只检测目标 3V3。廉价 ST-Link 上标为 `3V3` 的针脚可能是电源输出，不一定是 VAPP/TVCC；没有用万用表和型号资料确认前，不要把它与已经由 USB 供电的 3V3 并联。所有设备必须共地。

## 4. 烧录

```bash
cd firmware/stm32f070_imu_encoder
source ../../.venv/bin/activate  # 若虚拟环境建在仓库根目录
pio run -e stm32f070f6p6 -t upload
```

如果提示找不到 ST-Link，先运行 `lsusb` 和 `pio device list`，确认 udev 规则已经加载并重新拔插。不要用 `sudo pio`，否则会产生 root 所有的构建缓存。

## 5. CP2102N 与采集命令

查找稳定设备名：

```bash
ls -l /dev/serial/by-id/
```

把下面的端口替换为实际 CP2102N 路径：

```bash
python tools/example_imu_encoder.py \
  --port /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_XXXX-if00-port0
```

完整录制：

```bash
python tools/combined_capture.py \
  --port /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_XXXX-if00-port0 \
  --baud 921600 --csv capture.csv --raw capture.bin
```

## 6. 到板验收顺序

1. 断电，用万用表电阻/蜂鸣档确认 5V-GND、3V3-GND 无硬短路。
2. 不接 IMU 和编码器，上电确认 5V、3V3 正常，CP2102N 能枚举；没有联合帧输出是正常的。
3. 连接 ST-Link，烧录并重新上电。
4. 只接 KT-EX9：应约 400 Hz 输出，`IMU_VALID=1`；编码器无效不应阻塞 IMU。
5. 再接 AS5047P 和磁钢：`ENCODER_VALID=1`，角度随旋转变化，错误位和奇偶校验错误不应持续出现。
6. 连续录制10分钟做快速验收：CRC错误、丢弃字节、IMU计数器缺口、RX/发送队列溢出、PC消费队列丢帧均应为0。
7. 连续录制3小时做交付压力测试：保存 CSV、原始 `.bin`、日志、固件哈希；统计实际频率以及 `sensor_gap_us` 的最小值、均值、标准差、P95、P99和最大值。
8. 最后接入 D405/VINS 电脑。IMU和编码器使用同一个 MCU 时间基准和同一联合包；D405 对齐继续沿用已经跑通的 VINS 时间映射，不要用 USB 包到达顺序替代 MCU 时间戳。

## 7. 通过与停止条件

软件离线测试通过不等于实物通过。只有完成上面的供电、IMU-only、编码器和3小时压力测试，才能标记 STM32 硬件验收完成。任一 bit4/bit5/bit6、CRC错误或计数器缺口出现，都保留原始数据和日志，停止客户交付并定位原因。

PlatformIO udev 规则依据官方文档：<https://docs.platformio.org/en/latest/core/installation/udev-rules.html>。ST-Link 的 VAPP/TVCC 是目标电压参考，SWD至少需要 GND、SWDIO、SWCLK，NRST用于可靠连接/复位；参考 ST UM1075：<https://www.st.com/resource/en/user_manual/dm00026748.pdf>。
