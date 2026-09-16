"""Device-hosted UMI web console; only Python's standard library is required."""
import argparse
import base64
import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit
import zipfile

from server import Application, AppError, Handler, ROOT, ThreadingHTTPServer, safe_file, friendly_error


class LocalBridge:
    def __init__(self, host):
        self.host = host

    def rpc(self, action, **args):
        encoded = base64.b64encode(json.dumps({'action': action, **args}).encode()).decode()
        result = subprocess.run(['/usr/bin/python3', '-B', str(ROOT / 'remote_agent.py'), encoded],
                                capture_output=True, timeout=45, check=False)
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise AppError('设备控制器没有返回有效结果，请查看服务日志。')
        if not value.get('ok'):
            raise AppError(value.get('error', '设备命令失败'))
        return value['data']

    def preview(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', 18080, timeout=25)
        try:
            connection.request(method, path, json.dumps(body or {}).encode(), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            value = json.loads(response.read(65536))
            if response.status >= 400:
                error = value.get('error', {})
                raise AppError(error.get('message') or error.get('code') or '预览请求失败')
            return value
        finally:
            connection.close()

    def open_preview_source(self):
        return socket.create_connection(('127.0.0.1', 18081), timeout=10)


class DeviceApplication(Application):
    def view(self):
        value = super().view()
        value['download_dir'] = '当前手机或电脑的浏览器下载目录'
        value['transfer_mode'] = 'browser'
        return value

    def transfer(self, rid):
        archive = safe_file(self.download_dir, rid + '.zip')
        partial = archive.with_suffix('.zip.part')
        try:
            manifest = self.bridge.rpc('manifest', recording_id=rid)
            total = manifest['total_bytes']
            if shutil.disk_usage(self.download_dir).free < total + 256 * 1024 * 1024:
                raise AppError('设备空间不足，无法准备下载包。')
            self.update_transfer(rid, state='copying', total_bytes=total, bytes_done=0)
            done = 0
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as output:
                for item in manifest['files']:
                    source = safe_file(Path(manifest['remote_root']), item['path'])
                    digest = hashlib.sha256()
                    size = 0
                    last_update = 0
                    with source.open('rb') as reader, output.open(rid + '/' + item['path'], 'w', force_zip64=True) as writer:
                        for block in iter(lambda: reader.read(1024 * 1024), b''):
                            digest.update(block)
                            writer.write(block)
                            size += len(block)
                            if time.monotonic() - last_update > 0.4:
                                self.update_transfer(rid, file=item['path'], bytes_done=done + size)
                                last_update = time.monotonic()
                    if size != item['size'] or digest.hexdigest() != item['sha256']:
                        raise AppError('录制文件校验失败，未提供下载包。')
                    done += size
            self.update_transfer(rid, state='verifying', bytes_done=done)
            latest = self.bridge.rpc('manifest', recording_id=rid)
            if latest['manifest_sha256'] != manifest['manifest_sha256']:
                raise AppError('录制清单发生变化，请重新准备下载包。')
            os.replace(partial, archive)
            self.update_transfer(rid, state='complete', bytes_done=done, total_bytes=total,
                                 destination=str(archive), manifest_sha256=manifest['manifest_sha256'],
                                 archive_size=archive.stat().st_size, file='', error='', finished_at=time.time())
        except Exception as error:
            logging.exception('Archive preparation failed')
            partial.unlink(missing_ok=True)
            self.update_transfer(rid, state='failed', error=friendly_error(error))

    def reconcile_snapshot(self, value):
        deletions = value.get('deletions', {}).get('operations', [])
        completed = {
            item.get('recording_id') for item in deletions
            if isinstance(item, dict) and item.get('state') == 'complete'
        }
        with self.state_lock:
            for rid in completed:
                if not isinstance(rid, str):
                    continue
                safe_file(self.download_dir, rid + '.zip').unlink(missing_ok=True)
                safe_file(self.download_dir, rid + '.zip.part').unlink(missing_ok=True)
                (self.state_dir / f'transfer-{rid}.json').unlink(missing_ok=True)
                self.transfers.pop(rid, None)


def byte_range(header, size):
    if header is None:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()):
        raise ValueError('Invalid byte range')
    first, last = match.groups()
    if not first:
        length = int(last)
        if length <= 0:
            raise ValueError('Invalid suffix')
        start, end = max(0, size - length), size - 1
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        raise ValueError('Range outside file')
    return start, end, True


class DeviceHandler(Handler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if not path.startswith('/api/downloads/'):
            return super().do_GET()
        if not self.allowed_host():
            return self.send_json(403, {'error': '不允许的访问地址。'})
        match = re.fullmatch(r'/api/downloads/(recording_[A-Za-z0-9_-]{1,160})\.zip', path)
        if not match:
            return self.send_json(404, {'error': '下载地址无效。'})
        rid = match[1]
        with self.app.state_lock:
            task = dict(self.app.transfers.get(rid, {}))
        archive = safe_file(self.app.download_dir, rid + '.zip')
        if task.get('state') != 'complete' or not archive.is_file():
            return self.send_json(404, {'error': '下载包尚未就绪，请先准备转存。'})
        size = archive.stat().st_size
        try:
            start, end, partial = byte_range(self.headers.get('Range'), size)
        except ValueError:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{rid}.zip"')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', '"' + task['manifest_sha256'] + '"')
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with archive.open('rb') as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        self.close_connection = True
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (ConnectionError, socket.timeout):
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--host', default=socket.gethostname())
    args = parser.parse_args()
    state = Path.home() / '.local/state/umi-device-web'
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    app = DeviceApplication(LocalBridge(args.host), state, state / 'exports')
    server = ThreadingHTTPServer((args.bind, args.port), DeviceHandler)
    server.daemon_threads = True
    server.app = app
    server.allowed_hosts = {f'{host}:{args.port}' for host in (args.host, '127.0.0.1', 'localhost', socket.gethostname())}
    app.start_polling()
    try:
        server.serve_forever()
    finally:
        app.stopping.set()
        server.server_close()


if __name__ == '__main__':
    main()
