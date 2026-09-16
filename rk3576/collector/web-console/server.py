"""Shared UMI control, preview, and HTTP request handling for the device console."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import socket
import threading
import time
from urllib.parse import urlsplit
import uuid


ROOT = Path(__file__).resolve().parent
ACTIVE = {'starting', 'recording', 'stop_requested', 'finalizing'}
LOGGER = logging.getLogger('umi-console')


class AppError(Exception):
    pass


def safe_file(root: Path, relative: str) -> Path:
    """Reject remote paths unsafe on either Windows or POSIX."""
    parts = PurePosixPath(relative).parts
    if (not parts or relative.startswith('/') or '\\' in relative or ':' in relative
            or any(p in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', p)
                   or p.endswith(('.', ' ')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p)
                   for p in parts)):
        raise AppError('文件路径不安全，已停止转存。')
    path = root.joinpath(*parts)
    if root.resolve() not in path.resolve().parents:
        raise AppError('文件路径超出了转存目录。')
    for parent in [path, *path.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise AppError('转存目录中存在符号链接。')
    return path


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


class Application:
    def __init__(self, bridge, state_dir, download_dir):
        self.bridge = bridge
        self.state_dir, self.download_dir = state_dir.resolve(), download_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.state_lock = threading.RLock()
        self.control_lock = threading.Lock()
        self.preview_lock = threading.RLock()
        self.snapshot = {'connected': False, 'error': '正在连接设备…'}
        self.preview_session = None
        self.preview_job_id = None
        self.preview_frames = 0
        self.transfers = {}
        self.stopping = threading.Event()
        self.last_list = 0
        self.snapshot_monotonic = None
        self.load_transfers()

    def load_transfers(self):
        for p in self.state_dir.glob('transfer-*.json'):
            try:
                task = json.loads(p.read_text(encoding='utf-8'))
                if task['state'] not in ('complete', 'failed'):
                    task.update(state='failed', error='上次转存被中断，可点击重试继续。')
                self.transfers[task['recording_id']] = task
            except (OSError, ValueError, KeyError):
                LOGGER.warning('Ignoring invalid transfer state %s', p.name)

    def save_transfer(self, task):
        target = self.state_dir / f"transfer-{task['recording_id']}.json"
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, target)

    def start_polling(self):
        def poll():
            while not self.stopping.is_set():
                try:
                    with self.control_lock:
                        value = self.bridge.rpc('snapshot')
                        observed_monotonic = time.monotonic()
                        value.update(connected=True, updated_at=time.time(), host=self.bridge.host)
                        self.reconcile_snapshot(value)
                        with self.state_lock:
                            self.snapshot = value
                            self.snapshot_monotonic = observed_monotonic
                        with self.preview_lock:
                            if self.preview_session and self.preview_job_id and (
                                    value['status']['job_id'] != self.preview_job_id
                                    or value['status']['state'] not in ACTIVE):
                                self.close_preview(self.preview_session)
                except Exception as error:
                    with self.state_lock:
                        self.snapshot = {**self.snapshot, 'connected': False,
                                         'error': friendly_error(error), 'host': self.bridge.host}
                self.stopping.wait(2)
        threading.Thread(target=poll, daemon=True, name='device-status').start()

    def reconcile_snapshot(self, value):
        """Allow a deployment-specific console to reconcile persisted side effects."""
        return None

    def view(self):
        with self.state_lock:
            value = dict(self.snapshot)
            status = dict(value.get('status') or {})
            if (value.get('connected') and status.get('capture_running') is True
                    and self.snapshot_monotonic is not None):
                age = max(0.0, time.monotonic() - self.snapshot_monotonic)
                for field in ('capture_elapsed_s', 'elapsed_s'):
                    seconds = status.get(field)
                    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                        status[field] = max(0.0, float(seconds) + age)
            value['status'] = status
            value['transfers'] = list(self.transfers.values())
            value['download_dir'] = str(self.download_dir)
        with self.preview_lock:
            value['owned_preview'] = self.preview_session
            value['preview_frames'] = self.preview_frames
        return value

    def start_capture(self, request_id, duration):
        with self.control_lock, self.preview_lock:
            current = self.bridge.rpc('snapshot')
            if current['status']['state'] in ACTIVE:
                return self.bridge.rpc('start', request_id=request_id, duration=duration)
            sid = current['preview'].get('session_id')
            if sid and sid != self.preview_session:
                raise AppError('其他窗口正在使用设备预览，请先关闭该预览。')
            if self.preview_session:
                self.close_preview(self.preview_session)
            self.bridge.rpc('reset_idle_preview')
            return self.bridge.rpc('start', request_id=request_id, duration=duration)

    def start_preview(self):
        with self.control_lock, self.preview_lock:
            if self.preview_session:
                try:
                    return self.renew_preview(self.preview_session)
                except AppError:
                    self.preview_session = None
            current = self.bridge.rpc('snapshot')
            device_id = current.get('identity', {}).get('device_id')
            if not device_id:
                raise AppError('设备尚未连接，请稍后重试。')
            capture = current['status']
            if capture['state'] in ACTIVE:
                if not capture.get('capture_running') or not current['preview_source_ready']:
                    raise AppError('相机正在启动或保存，请稍后开启预览。')
            else:
                self.bridge.rpc('reset_idle_preview')
            result = self.bridge.preview('POST', '/sessions', {'device_id': device_id})
            self.preview_session = result['session_id']
            self.preview_job_id = capture.get('job_id') if capture['state'] in ACTIVE else None
            self.preview_frames = 0
            return result

    def check_session(self, sid):
        if not isinstance(sid, str) or not re.fullmatch(r'[0-9a-f]{32}', sid) or sid != self.preview_session:
            raise AppError('预览会话已结束，请重新开启。')

    def renew_preview(self, sid):
        with self.preview_lock:
            self.check_session(sid)
            return self.bridge.preview('POST', f'/sessions/{sid}/renew', {})

    def close_preview(self, sid):
        with self.preview_lock:
            self.check_session(sid)
            try:
                return self.bridge.preview('POST', f'/sessions/{sid}/close', {'reason': 'WEB_CLOSED'})
            finally:
                self.preview_session = None
                self.preview_job_id = None

    def start_transfer(self, rid):
        if not isinstance(rid, str) or not re.fullmatch(r'recording_[A-Za-z0-9_-]{1,160}', rid):
            raise AppError('录制编号无效。')
        with self.state_lock:
            existing = self.transfers.get(rid)
            if existing and existing['state'] not in ('failed',):
                return existing.copy()
            if any(t['state'] in ('preparing', 'copying', 'verifying') for t in self.transfers.values()):
                raise AppError('正在转存另一条录制，请等待完成。')
            task = {'recording_id': rid, 'state': 'preparing', 'bytes_done': 0, 'total_bytes': 0,
                    'file': '', 'error': '', 'destination': str(self.download_dir / rid), 'started_at': time.time()}
            self.transfers[rid] = task
            self.save_transfer(task)
        threading.Thread(target=self.transfer, args=(rid,), daemon=True, name='recording-transfer').start()
        return task.copy()

    def delete_recording(self, rid, request_id):
        if not isinstance(rid, str) or not re.fullmatch(r'recording_[A-Za-z0-9_-]{1,160}', rid):
            raise AppError('录制编号无效。')
        try:
            request_id = str(uuid.UUID(str(request_id)))
        except (TypeError, ValueError, AttributeError) as error:
            raise AppError('删除请求编号无效。') from error
        with self.state_lock:
            transfer = self.transfers.get(rid)
            if transfer and transfer.get('state') in ('preparing', 'copying', 'verifying'):
                raise AppError('该录制正在转存，请等待转存结束后再删除。')
        with self.control_lock:
            return self.bridge.rpc('delete_recording', recording_id=rid, request_id=request_id)

    def update_transfer(self, rid, **values):
        with self.state_lock:
            self.transfers[rid].update(values)
            self.save_transfer(self.transfers[rid])

    def transfer(self, rid):
        sftp = None
        try:
            manifest = self.bridge.rpc('manifest', recording_id=rid)
            stage_root = self.state_dir / 'partial-downloads'
            stage_root.mkdir(exist_ok=True)
            stage = safe_file(stage_root, rid)
            final = safe_file(self.download_dir, rid)
            if final.exists():
                if all(safe_file(final, f['path']).is_file() and sha256(safe_file(final, f['path'])) == f['sha256']
                       for f in manifest['files']):
                    self.update_transfer(rid, state='complete', total_bytes=manifest['total_bytes'],
                                         bytes_done=manifest['total_bytes'], file='', error='')
                    return
                raise AppError('目标目录已存在且校验不一致，已保留原文件。')
            stage.mkdir(exist_ok=True)
            if shutil.disk_usage(stage).free < manifest['total_bytes'] + 128 * 1024 * 1024:
                raise AppError('电脑剩余空间不足，无法转存这条录制。')
            self.update_transfer(rid, state='copying', total_bytes=manifest['total_bytes'])
            sftp = self.bridge.sftp()
            sftp.get_channel().settimeout(25)
            completed = 0
            for item in manifest['files']:
                path = safe_file(stage, item['path'])
                path.parent.mkdir(parents=True, exist_ok=True)
                self.update_transfer(rid, state='copying', file=item['path'], bytes_done=completed)
                if path.exists() and path.stat().st_size == item['size'] and sha256(path) == item['sha256']:
                    completed += item['size']
                    self.update_transfer(rid, bytes_done=completed)
                    continue
                if path.exists():
                    path.unlink()
                partial = safe_file(stage, item['path'] + '.part')
                if partial.exists() and partial.stat().st_size > item['size']:
                    partial.unlink()
                offset = partial.stat().st_size if partial.exists() else 0
                remote_path = manifest['remote_root'] + '/' + item['path']
                last_update = 0
                with sftp.open(remote_path, 'rb') as source, partial.open('ab') as target:
                    source.seek(offset)
                    while offset < item['size']:
                        chunk = source.read(min(256 * 1024, item['size'] - offset))
                        if not chunk:
                            raise AppError('设备文件在传输中提前结束，可重试续传。')
                        target.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last_update > 0.4:
                            self.update_transfer(rid, bytes_done=completed + offset)
                            last_update = time.monotonic()
                    target.flush()
                    os.fsync(target.fileno())
                self.update_transfer(rid, state='verifying', bytes_done=completed + offset)
                if sha256(partial) != item['sha256']:
                    partial.unlink()
                    raise AppError('文件校验未通过，已丢弃损坏的临时文件，请重试。')
                os.replace(partial, path)
                completed += item['size']
            # Check immutable publication identity again before publishing the local directory.
            latest = self.bridge.rpc('manifest', recording_id=rid)
            if latest['manifest_sha256'] != manifest['manifest_sha256']:
                raise AppError('设备上的录制清单发生变化，已停止完成转存。')
            if stage.resolve().parent != stage_root.resolve() or final.resolve().parent != self.download_dir:
                raise AppError('转存目录检查失败。')
            os.rename(stage, final)
            self.update_transfer(rid, state='complete', bytes_done=completed, file='', error='', finished_at=time.time())
        except Exception as error:
            LOGGER.exception('Transfer failed')
            self.update_transfer(rid, state='failed', error=friendly_error(error))
        finally:
            if sftp:
                sftp.close()


def friendly_error(error):
    message = str(error)
    mapping = {'PREVIEW_BUSY': '预览正在被其他客户端使用。', 'PROCESS_NOT_READY': '采集正在启动，请稍后再停止。',
               'ALREADY_RECORDING': '设备已有采集任务。', 'PREFLIGHT_FAILED': '相机、串口或采集程序尚未就绪。',
               'LOCAL_SPACE_LOW': '设备剩余空间不足 2 GiB。', 'JOB_NOT_ACTIVE': '此录制已结束。',
               'PREVIEW_SESSION_NOT_FOUND': '预览会话已过期，请重新开启。',
               'ACTIVE_JOB': '录制或保存期间不能删除数据。',
               'MAINTENANCE_BUSY': '设备正在执行另一项维护操作，请稍后重试。',
               'RECORDING_CHANGED': '录制文件与清单不一致，已拒绝删除。',
               'LOCAL_DELETE_FAILED': '录制数据删除失败，原始状态已保留。',
               'CATALOG_UPDATE_FAILED': '数据已处理，但目录状态更新失败，请重试恢复。'}
    for key, value in mapping.items():
        if key in message:
            return value
    if isinstance(error, (socket.timeout, TimeoutError)):
        return '设备响应超时，请检查网络后重试。'
    return message[:700] or '操作失败，请稍后重试。'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    @property
    def app(self):
        return self.server.app

    def allowed_host(self):
        local_address = self.connection.getsockname()[0]
        dynamic = f'{local_address}:{self.server.server_port}'
        return self.headers.get('Host') in {*self.server.allowed_hosts, dynamic}

    def allowed_origin(self):
        host = self.headers.get('Host')
        return self.allowed_host() and self.headers.get('Origin') == f'http://{host}'

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():
            self.send_json(403, {'error': '不允许的访问地址。'})
            return
        path = urlsplit(self.path).path
        try:
            if path == '/api/bootstrap':
                self.send_json(200, {'token': self.app.token, 'host': self.app.bridge.host})
            elif path == '/api/state':
                self.send_json(200, self.app.view())
            elif path.startswith('/api/preview/stream/'):
                sid = path.rsplit('/', 1)[-1]
                self.app.check_session(sid)
                self.stream_preview(sid)
            elif path in ('/', '/index.html', '/clock.js', '/app.js', '/style.css', '/favicon.svg'):
                filename = 'index.html' if path == '/' else path[1:]
                content = (ROOT / 'static' / filename).read_bytes()
                content_type = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8',
                                'css': 'text/css; charset=utf-8', 'svg': 'image/svg+xml'}[filename.rsplit('.', 1)[-1]]
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_json(404, {'error': '页面不存在。'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.send_json(502, {'error': friendly_error(error)})

    def stream_preview(self, sid):
        source = None
        headers_sent = False
        try:
            # Read the native JPEG stream directly. The packaged HTTP proxy
            # restarts cameras on EOF, including during recording transitions.
            source = self.app.bridge.open_preview_source()
            source.settimeout(10)
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            headers_sent = True
            self.close_connection = True
            buffer = bytearray()
            while sid == self.app.preview_session:
                chunk = source.recv(64 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9', start + 2) if start >= 0 else -1
                    if end < 0:
                        if len(buffer) > 2 * 1024 * 1024:
                            raise AppError('预览画面数据无效，请重新开启。')
                        break
                    frame = bytes(buffer[start:end + 2])
                    del buffer[:end + 2]
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                     + str(len(frame)).encode('ascii') + b'\r\n\r\n' + frame + b'\r\n')
                    self.wfile.flush()
                    self.app.preview_frames += 1
        except (ConnectionError, socket.timeout):
            pass
        except Exception:
            LOGGER.exception('Preview stream failed')
            if not headers_sent:
                raise
        finally:
            if source is not None:
                source.close()

    def do_POST(self):
        if (not self.allowed_origin()
                or not hmac.compare_digest(self.headers.get('X-UMI-Token', ''), self.app.token)):
            self.send_json(403, {'error': '请求校验失败，请刷新网页。'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 4096 or not self.headers.get('Content-Type', '').startswith('application/json'):
                raise AppError('请求格式无效。')
            data = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(data, dict):
                raise AppError('请求格式无效。')
            path = urlsplit(self.path).path
            if path == '/api/start':
                duration = data.get('duration', 0)
                rid = data.get('request_id')
                if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 86400:
                    raise AppError('采集时长应为 0–86400 秒。')
                if str(uuid.UUID(str(rid))) != rid:
                    raise AppError('请求编号无效。')
                result = self.app.start_capture(rid, duration)
            elif path == '/api/stop':
                with self.app.control_lock:
                    result = self.app.bridge.rpc('stop', job_id=data.get('job_id'))
            elif path == '/api/preview/start':
                result = self.app.start_preview()
            elif path == '/api/preview/renew':
                result = self.app.renew_preview(data.get('session_id'))
            elif path == '/api/preview/stop':
                result = self.app.close_preview(data.get('session_id'))
            elif path == '/api/transfers':
                result = self.app.start_transfer(data.get('recording_id'))
            elif path == '/api/delete-recording':
                result = self.app.delete_recording(
                    data.get('recording_id'), data.get('request_id')
                )
            elif path == '/api/open-folder':
                rid = data.get('recording_id')
                with self.app.state_lock:
                    task = self.app.transfers.get(rid)
                if not task or task['state'] != 'complete':
                    raise AppError('请先完成转存。')
                folder = safe_file(self.app.download_dir, rid)
                if not folder.is_dir():
                    raise AppError('转存目录已被移动或删除。')
                if os.name != 'nt':
                    raise AppError('当前系统不支持自动打开文件夹。')
                os.startfile(folder)
                result = {'opened': True}
            elif path == '/api/logs':
                result = self.app.bridge.rpc('logs', job_id=data.get('job_id'))
            else:
                self.send_json(404, {'error': '接口不存在。'})
                return
            self.send_json(200, {'ok': True, 'data': result})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            LOGGER.warning('Request failed: %s', friendly_error(error))
            self.send_json(400 if isinstance(error, (AppError, ValueError)) else 502,
                           {'error': friendly_error(error)})

    def log_message(self, fmt, *args):
        if not args or str(args[0]).split(' ')[1:2] not in (['/api/state'],):
            LOGGER.info(fmt, *args)
