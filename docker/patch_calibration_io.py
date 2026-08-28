#!/usr/bin/env python3
"""Decouple D405 calibration image and IMU persistence queues."""

from pathlib import Path


TARGET = Path("/home/robot/ego_vio_humble/scripts/collect_calib_data.py")

OLD_QUEUES = '''    # 两个独立队列, 避免写盘线程和质检线程竞争同一份数据
    q_write = Queue(maxsize=120)
    q_quality = Queue(maxsize=60)  # 质检队列满时丢帧, 不阻塞采集'''

NEW_QUEUES = '''    # 采集持久化按传感器解耦。双IR JPEG偶发慢写不得反压400Hz
    # 串口；IMU写盘抖动也不得阻塞RealSense wait_for_frames。
    q_camera_write = Queue(maxsize=120)  # 约4秒双IR缓冲
    q_imu_write = Queue(maxsize=4000)    # 约10秒IMU缓冲
    q_quality = Queue(maxsize=60)  # 质检队列满时丢帧, 不阻塞采集'''

OLD_WRITER = '''    def writer_loop():
        nonlocal written_frames, written_imu
        while True:
            item = q_write.get()
            if item is None:
                break
            kind = item[0]
            try:
                if kind == "img":
                    _, idx, ts, img, img_right, ts_wall, fnum = item
                    path = frames_dir / f"{idx:06d}.jpg"
                    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                    if ok:
                        buf.tofile(path)
                        written_frames += 1
                    if img_right is not None:
                        path_r = frames_right_dir / f"{idx:06d}.jpg"
                        ok_r, buf_r = cv2.imencode(".jpg", img_right,
                                                    [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                        if ok_r:
                            buf_r.tofile(path_r)
                    cam_w.writerow([idx, fnum, f"{ts:.9f}", f"{ts_wall:.6f}", 0])
                elif kind == "imu":
                    s = item[1]
                    imu_bin.write(struct.pack(
                        IMU_PACK_FMT, s.ts, s.counter,
                        s.gx, s.gy, s.gz, s.ax, s.ay, s.az, s.temp,
                    ))
                    imu_w.writerow([
                        s.counter,
                        f"{s.ts:.9f}",
                        f"{s.rx_time:.9f}",
                        f"{time.time():.6f}",
                    ])
                    written_imu += 1
            except Exception as e:
                print(f"[写盘错误] {e}")

    writer = threading.Thread(target=writer_loop, daemon=True)
    writer.start()'''

NEW_WRITER = '''    def camera_writer_loop():
        nonlocal written_frames
        while True:
            item = q_camera_write.get()
            if item is None:
                break
            try:
                _, idx, ts, img, img_right, ts_wall, fnum, arrival_mono = item
                path = frames_dir / f"{idx:06d}.jpg"
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if ok:
                    buf.tofile(path)
                    written_frames += 1
                if img_right is not None:
                    path_r = frames_right_dir / f"{idx:06d}.jpg"
                    ok_r, buf_r = cv2.imencode(".jpg", img_right,
                                                [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                    if ok_r:
                        buf_r.tofile(path_r)
                cam_w.writerow([
                    idx, fnum, f"{ts:.9f}", f"{arrival_mono:.9f}",
                    f"{ts_wall:.6f}", 0,
                ])
            except Exception as e:
                print(f"[相机写盘错误] {e}")

    def imu_writer_loop():
        nonlocal written_imu
        while True:
            item = q_imu_write.get()
            if item is None:
                break
            try:
                s = item[1]
                imu_bin.write(struct.pack(
                    IMU_PACK_FMT, s.ts, s.counter,
                    s.gx, s.gy, s.gz, s.ax, s.ay, s.az, s.temp,
                ))
                imu_w.writerow([
                    s.counter,
                    f"{s.ts:.9f}",
                    f"{s.rx_time:.9f}",
                    f"{time.time():.6f}",
                ])
                written_imu += 1
            except Exception as e:
                print(f"[IMU写盘错误] {e}")

    camera_writer = threading.Thread(target=camera_writer_loop, daemon=True)
    imu_writer = threading.Thread(target=imu_writer_loop, daemon=True)
    camera_writer.start()
    imu_writer.start()'''

OLD_STOP = '''    q_write.put(None)
    writer.join(timeout=5.0)'''

NEW_STOP = '''    q_camera_write.put(None)
    q_imu_write.put(None)
    # 传感器已停止，队列有限；必须完整排空后才能关闭文件并签发健康报告。
    camera_writer.join()
    imu_writer.join()'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(text, OLD_QUEUES, NEW_QUEUES, "queue declaration")
    text = replace_once(
        text,
        'item = ("img", f.frame_idx, f.ts, f.color, right,\n'
        '                    time.time(), f.frame_number)',
        'item = ("img", f.frame_idx, f.ts, f.color, right,\n'
        '                    time.time(), f.frame_number, f.ts_arrival)',
        "camera raw monotonic timestamp",
    )
    text = replace_once(
        text,
        'cam_w.writerow(["idx", "frame_number", "ts_mono", "ts_wall", "has_depth"])',
        'cam_w.writerow(["idx", "frame_number", "ts_mono", "arrival_mono", '
        '"ts_wall", "has_depth"])',
        "camera timestamp header",
    )
    text = replace_once(text, "\n        q_write.put(item)\n",
                        "\n        q_imu_write.put(item)\n", "IMU enqueue")
    text = replace_once(text, "\n            q_write.put(item)\n",
                        "\n            q_camera_write.put(item)\n", "camera enqueue")
    text = replace_once(text, OLD_WRITER, NEW_WRITER, "writer implementation")
    text = replace_once(text, OLD_STOP, NEW_STOP, "writer shutdown")
    TARGET.write_text(text, encoding="utf-8")

    patched = TARGET.read_text(encoding="utf-8")
    required = (
        "q_camera_write", "q_imu_write", "camera_writer_loop",
        "imu_writer_loop", "f.ts_arrival", '"arrival_mono"',
    )
    if any(token not in patched for token in required):
        raise SystemExit("split writer verification failed")
    if "q_write = Queue" in patched or "q_write.put" in patched:
        raise SystemExit("shared blocking writer queue remains")


if __name__ == "__main__":
    main()
