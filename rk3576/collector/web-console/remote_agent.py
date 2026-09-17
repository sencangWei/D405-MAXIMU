"""Small JSON bridge, executed over authenticated SSH; never installed on the board."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error


def run(request):
    release = Path('/home/pi/umi-collector/current').resolve()
    settings = Path('/home/pi/.config/umi-recorder/umi-recorder.env')
    for line in settings.read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        parsed = shlex.split(value)
        if len(parsed) != 1 or not key.startswith(('UMI_', 'EGO_')):
            raise ValueError('Invalid device environment entry')
        os.environ[key] = parsed[0]
    runtime_paths = [str(release / p) for p in ('adapter', 'vendor/ego-runtime', 'vendor/site-packages')]
    sys.path[:0] = runtime_paths
    # The existing controller spawns a new Python worker: it must inherit the
    # same packaged imports as the official bin/recorderctl launcher.
    os.environ['PYTHONPATH'] = os.pathsep.join(runtime_paths)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    import umi_recorderctl as controller
    cfg = controller.Config()

    def control(args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            controller.main(args + ['--json'])
        value = json.loads(output.getvalue())
        if not value.get('ok'):
            raise RuntimeError(json.dumps(value.get('error', {}), ensure_ascii=False))
        return value['data']

    def catalog():
        if not cfg.catalog_db.exists():
            return []
        with sqlite3.connect(cfg.catalog_db.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM recordings WHERE save_state != 'SOURCE_DELETED' "
                "ORDER BY recorded_at DESC"
            )]

    def preview_health():
        with urllib.request.urlopen('http://127.0.0.1:18080/healthz', timeout=3) as response:
            return json.load(response)

    def source_listens():
        port = f"{int(os.environ.get('UMI_PREVIEW_SOURCE_PORT', '18081')):04X}"
        for table in ('/proc/net/tcp', '/proc/net/tcp6'):
            for line in Path(table).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[1].rsplit(':', 1)[-1] == port and fields[3] == '0A':
                    return True
        return False

    action = request['action']
    if action == 'snapshot':
        state = control(['status'])
        if cfg.current.exists():
            raw = json.loads(cfg.current.read_text())
            state['error_detail'] = raw.get('error', '')
        disk = shutil.disk_usage(cfg.recording_root)
        temps = []
        for p in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
            try:
                temps.append(int(p.read_text()) / 1000)
            except (OSError, ValueError):
                pass
        try:
            preview = preview_health()
        except Exception:
            preview = {'ok': False}
        records = catalog()
        deletions = controller.delete_status(cfg)
        incomplete = controller.open_incomplete_list(cfg)
        incomplete_operations = controller.incomplete_status(cfg)['operations']
        return {'identity': controller.identity(cfg), 'status': state, 'preview': preview,
                'incomplete': incomplete, 'incomplete_operations': incomplete_operations,
                'disk': {'free': disk.free, 'total': disk.total}, 'temperature': max(temps) if temps else None,
                'camera_present': any((p / 'serial').is_file() and (p / 'serial').read_text().strip() == cfg.usb_serial
                                      for p in Path('/sys/bus/usb/devices').iterdir()),
                'serial_present': Path(cfg.stm32_port).exists(), 'recordings': records,
                'deletions': deletions, 'preview_source_ready': source_listens()}
    if action == 'reset_idle_preview':
        if control(['status'])['state'] in ('starting', 'recording', 'stop_requested', 'finalizing'):
            raise ValueError('采集尚未结束，不能重新初始化预览。')
        if preview_health().get('session_id'):
            raise ValueError('其他窗口正在使用预览，请先关闭该预览。')
        # Clear only the preview service's process group. Its old automatic
        # reconnect worker can otherwise reopen the D405 during a handoff.
        subprocess.run(['systemctl', '--user', 'restart', 'umi-preview.service'],
                       check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(30):
            try:
                if preview_health().get('ok'):
                    return {'ready': True}
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.1)
        raise RuntimeError('预览服务未能就绪，请稍后重试。')
    if action == 'start':
        return control(['start', '--request-id', request['request_id'], '--duration', str(request['duration'])])
    if action == 'stop':
        job = request.get('job_id')
        if not isinstance(job, str) or not re.fullmatch(r'umi-[0-9a-f]{32}', job):
            raise ValueError('Invalid job ID')
        return control(['stop', '--job-id', job])
    if action == 'logs':
        job = request.get('job_id')
        if not isinstance(job, str) or not re.fullmatch(r'umi-[0-9a-f]{32}', job):
            raise ValueError('Invalid job ID')
        result = control(['logs', '--job-id', job, '--limit', '30'])
        return result
    if action == 'delete_recording':
        rid = request.get('recording_id', '')
        request_id = request.get('request_id', '')
        if not re.fullmatch(r'recording_[A-Za-z0-9_-]{1,160}', rid):
            raise ValueError('Invalid recording ID')
        return control([
            'delete-recording', '--recording-id', rid,
            '--request-id', request_id,
        ])
    if action == 'incomplete_list':
        return control(['incomplete-list'])
    if action == 'incomplete_status':
        return control(['incomplete-status'])
    if action == 'incomplete_recover':
        session = request.get('session', '')
        if not re.fullmatch(r'rk3576-rsusb-cpp-\d{8}T\d{6}Z-[0-9a-f]{8}', session):
            raise ValueError('Invalid incomplete session')
        assets = request.get('assets', 'all')
        if assets not in ('all', 'rgb', 'ir'):
            raise ValueError('Invalid asset selection')
        args = ['incomplete-recover', '--session', session,
                '--request-id', request.get('request_id', ''), '--assets', assets]
        if request.get('delete_remainder'):
            args.append('--delete-remainder')
        if request.get('dry_run'):
            args.append('--dry-run')
        return control(args)
    if action == 'incomplete_delete':
        session = request.get('session', '')
        if not re.fullmatch(r'rk3576-rsusb-cpp-\d{8}T\d{6}Z-[0-9a-f]{8}', session):
            raise ValueError('Invalid incomplete session')
        return control([
            'incomplete-delete', '--session', session,
            '--request-id', request.get('request_id', ''),
        ])
    if action == 'manifest':
        import hashlib
        rid = request.get('recording_id', '')
        if not re.fullmatch(r'recording_[A-Za-z0-9_-]{1,160}', rid):
            raise ValueError('Invalid recording ID')
        row = next((row for row in catalog() if row['recording_id'] == rid), None)
        if not row or row['state'] != 'COMPLETE_LOCAL':
            raise ValueError('Recording is not published and complete')
        base = cfg.recording_root / 'recordings-v2' / 'completed' / rid
        if base.is_symlink() or base.resolve().parent != (cfg.recording_root / 'recordings-v2/completed').resolve():
            raise ValueError('Unsafe recording directory')
        manifest_path = base / 'MANIFEST.sha256'
        if manifest_path.is_symlink():
            raise ValueError('Unsafe manifest')
        raw = manifest_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['manifest_sha256']:
            raise ValueError('Catalog manifest hash mismatch')
        files = []
        seen = set()
        for line in raw.decode('utf-8').splitlines():
            digest, relative = line.split('  ', 1)
            parts = PurePosixPath(relative).parts
            if (not re.fullmatch(r'[0-9a-f]{64}', digest) or not parts or relative in seen
                    or relative.startswith('/') or any(p in ('.', '..') for p in parts)
                    or '\\' in relative or ':' in relative):
                raise ValueError('Unsafe file entry in manifest')
            seen.add(relative)
            path = base / relative
            if any((base.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
                raise ValueError('Recording contains a symlink')
            if not path.is_file() or base.resolve() not in path.resolve().parents:
                raise ValueError('Recording asset missing or outside directory')
            files.append({'path': relative, 'size': path.stat().st_size, 'sha256': digest})
        files.append({'path': 'MANIFEST.sha256', 'size': len(raw), 'sha256': row['manifest_sha256']})
        return {'recording_id': rid, 'remote_root': str(base), 'files': files,
                'manifest_sha256': row['manifest_sha256'], 'total_bytes': sum(f['size'] for f in files)}
    raise ValueError('Unknown action')


if __name__ == '__main__':
    try:
        result = run(json.loads(base64.b64decode(sys.argv[1])))
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
