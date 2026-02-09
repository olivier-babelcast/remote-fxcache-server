#!/usr/bin/env python3
"""
Remote FXCACHE server.

Serves cache files over HTTP for LAN cache sharing.
Uses SQLite for fast file existence checks.

Usage:
    python server.py
    python server.py --port 5002
    python server.py --fxcache-path /Volumes/FD_1TB_EXT/FXCACHE
"""

import argparse
import json as _json
import logging
import os
import socket
import threading
from datetime import datetime

from flask import Flask, jsonify, request, send_file

from db import FxcacheDB

app = Flask(__name__)

# In-memory store for debug logs (keyed by machine name)
_debug_logs: dict[str, dict] = {}

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Globals set at startup
db: FxcacheDB = None
FXCACHE_PATH: str = ''
_upload_lock = threading.Lock()

# Stats
stats = {
    'downloads': 0,
    'uploads': 0,
    'exists_checks': 0,
    'errors': 0,
    'bytes_sent': 0,
    'bytes_received': 0,
    'start_time': None,
}


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _validate_path(rel_path: str) -> str | None:
    """Validate and normalize a relative path. Returns None if invalid."""
    if not rel_path:
        return None
    if '..' in rel_path or rel_path.startswith('/') or '\x00' in rel_path:
        return None
    normalized = os.path.normpath(rel_path)
    if normalized.startswith('..'):
        return None
    return normalized


# --- Endpoints ---

@app.route('/health', methods=['GET'])
def health():
    uptime = (datetime.now() - stats['start_time']).total_seconds() if stats['start_time'] else 0
    return jsonify({
        'status': 'ok',
        'fxcache_path': FXCACHE_PATH,
        'db_file_count': db.file_count(),
        'last_refresh': db._get_meta('last_refresh'),
        'uptime_seconds': round(uptime, 1),
        'stats': stats,
    })


@app.route('/exists', methods=['GET'])
def exists():
    rel_path = request.args.get('path', '')
    validated = _validate_path(rel_path)
    if validated is None:
        return jsonify({'error': 'Invalid path'}), 400
    stats['exists_checks'] += 1
    return jsonify(db.exists(validated))


@app.route('/exists_batch', methods=['POST'])
def exists_batch():
    data = request.get_json(silent=True)
    if not data or 'paths' not in data:
        return jsonify({'error': 'Missing paths field'}), 400
    paths = data['paths']
    if not isinstance(paths, list) or len(paths) > 500:
        return jsonify({'error': 'paths must be a list of max 500 items'}), 400
    validated = []
    for p in paths:
        v = _validate_path(p)
        if v is None:
            return jsonify({'error': f'Invalid path: {p}'}), 400
        validated.append(v)
    stats['exists_checks'] += len(validated)
    return jsonify({'results': db.exists_batch(validated)})


@app.route('/download', methods=['GET'])
def download():
    rel_path = request.args.get('path', '')
    validated = _validate_path(rel_path)
    if validated is None:
        return jsonify({'error': 'Invalid path'}), 400
    full_path = os.path.join(FXCACHE_PATH, validated)
    if not os.path.isfile(full_path):
        return jsonify({'error': 'File not found'}), 404
    try:
        size = os.path.getsize(full_path)
        stats['downloads'] += 1
        stats['bytes_sent'] += size
        return send_file(full_path, mimetype='application/octet-stream')
    except Exception as e:
        stats['errors'] += 1
        logger.error(f"Download error for {validated}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/upload', methods=['POST'])
def upload():
    rel_path = request.args.get('path', '')
    validated = _validate_path(rel_path)
    if validated is None:
        return jsonify({'error': 'Invalid path'}), 400
    full_path = os.path.join(FXCACHE_PATH, validated)
    data = request.get_data()
    if not data:
        return jsonify({'error': 'Empty body'}), 400
    try:
        with _upload_lock:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'wb') as f:
                f.write(data)
        size = len(data)
        mtime = os.path.getmtime(full_path)
        db.upsert(validated, size, mtime)
        stats['uploads'] += 1
        stats['bytes_received'] += size
        logger.info(f"Uploaded {validated} ({size} bytes)")
        return jsonify({'status': 'ok', 'path': validated, 'size': size})
    except Exception as e:
        stats['errors'] += 1
        logger.error(f"Upload error for {validated}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/refresh', methods=['POST'])
def refresh():
    mode = request.args.get('mode', 'auto')  # 'auto', 'full', or 'incremental'
    if mode not in ('auto', 'full', 'incremental'):
        return jsonify({'error': 'Invalid mode. Use: auto, full, incremental'}), 400
    started = db.refresh(mode=mode)
    if not started:
        return jsonify({'status': 'already_running'}), 409
    return jsonify({'status': 'started', 'mode': mode, 'message': 'Database refresh started in background'})


@app.route('/refresh/status', methods=['GET'])
def refresh_status():
    return jsonify(db.get_refresh_status())


# --- Debug log sharing ---

@app.route('/debug/log', methods=['POST'])
def debug_log_post():
    """Post a debug log from a machine. Body: JSON with 'machine' key."""
    data = request.get_json(silent=True)
    if not data or 'machine' not in data:
        return jsonify({'error': 'Missing machine field'}), 400
    machine = data['machine']
    data['posted_at'] = datetime.now().isoformat()
    _debug_logs[machine] = data
    logger.info(f"Debug log received from '{machine}' ({len(_json.dumps(data))} bytes)")
    return jsonify({'status': 'ok', 'machine': machine})


@app.route('/debug/log', methods=['GET'])
def debug_log_get():
    """Get debug logs. ?machine=X for specific, or all."""
    machine = request.args.get('machine', '')
    if machine:
        log = _debug_logs.get(machine)
        if log is None:
            return jsonify({'error': f'No log for machine: {machine}'}), 404
        return jsonify(log)
    return jsonify(_debug_logs)


@app.route('/debug/log/list', methods=['GET'])
def debug_log_list():
    """List all machines that have posted debug logs."""
    return jsonify({
        'machines': list(_debug_logs.keys()),
        'count': len(_debug_logs),
    })


@app.route('/debug/compare', methods=['GET'])
def debug_compare():
    """Compare debug logs from two machines. Shows only differences."""
    machines = list(_debug_logs.keys())
    if len(machines) < 2:
        return jsonify({'error': f'Need 2 logs, have {len(machines)}: {machines}'}), 400
    m1 = request.args.get('m1', machines[0])
    m2 = request.args.get('m2', machines[1])
    log1 = _debug_logs.get(m1)
    log2 = _debug_logs.get(m2)
    if not log1 or not log2:
        return jsonify({'error': f'Missing log for {m1 if not log1 else m2}'}), 404

    diffs = _diff_logs(log1, log2, m1, m2)
    return jsonify({'m1': m1, 'm2': m2, 'diffs': diffs, 'match': len(diffs) == 0})


def _diff_logs(log1: dict, log2: dict, name1: str, name2: str) -> list:
    """Recursively compare two debug log dicts, return list of differences."""
    diffs = []
    skip_keys = {'machine', 'posted_at', 'hostname'}
    all_keys = set(log1.keys()) | set(log2.keys())
    for key in sorted(all_keys - skip_keys):
        v1 = log1.get(key)
        v2 = log2.get(key)
        if isinstance(v1, list) and isinstance(v2, list) and len(v1) == len(v2):
            # Compare list items (e.g. processors_shas)
            for i, (item1, item2) in enumerate(zip(v1, v2)):
                if isinstance(item1, dict) and isinstance(item2, dict):
                    for k in sorted(set(item1.keys()) | set(item2.keys())):
                        if item1.get(k) != item2.get(k):
                            diffs.append({
                                'key': f'{key}[{i}].{k}',
                                name1: item1.get(k),
                                name2: item2.get(k),
                            })
                elif item1 != item2:
                    diffs.append({'key': f'{key}[{i}]', name1: item1, name2: item2})
        elif v1 != v2:
            # Truncate long strings for readability
            d = {'key': key}
            d[name1] = str(v1)[:500] if isinstance(v1, str) and len(str(v1)) > 500 else v1
            d[name2] = str(v2)[:500] if isinstance(v2, str) and len(str(v2)) > 500 else v2
            diffs.append(d)
    return diffs


# --- Startup ---

def print_startup_banner(host: str, port: int):
    local_ip = get_local_ip()
    print()
    print("=" * 60)
    print("Remote FXCACHE Server")
    print("=" * 60)
    print(f"Local IP:    {local_ip}")
    print(f"Port:        {port}")
    print(f"URL:         http://{local_ip}:{port}")
    print(f"FXCACHE:     {FXCACHE_PATH}")
    print(f"DB files:    {db.file_count()}")
    print("=" * 60)
    print()
    print("Endpoints:")
    print(f"  GET  /health          - Health check")
    print(f"  GET  /exists?path=    - Check file existence (fast)")
    print(f"  POST /exists_batch    - Batch existence check")
    print(f"  GET  /download?path=  - Download file")
    print(f"  POST /upload?path=    - Upload file")
    print(f"  POST /refresh         - Rebuild SQLite index")
    print(f"  GET  /refresh/status  - Refresh progress")
    print(f"  POST /debug/log       - Post debug log from a machine")
    print(f"  GET  /debug/log       - Get debug logs (?machine=X)")
    print(f"  GET  /debug/log/list  - List machines with logs")
    print(f"  GET  /debug/compare   - Compare logs from two machines")
    print()


def main():
    global db, FXCACHE_PATH

    parser = argparse.ArgumentParser(description='Remote FXCACHE server')
    parser.add_argument('--port', type=int, default=int(os.environ.get('FXCACHE_SERVER_PORT', 5002)),
                        help='Port to listen on (default: 5002)')
    parser.add_argument('--host', type=str, default=os.environ.get('FXCACHE_SERVER_HOST', '0.0.0.0'),
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--fxcache-path', type=str,
                        default=os.environ.get('FXCACHE_PATH', '/Volumes/FD_1TB_EXT/FXCACHE'),
                        help='Path to FXCACHE directory')
    args = parser.parse_args()

    FXCACHE_PATH = os.path.realpath(args.fxcache_path)
    if not os.path.isdir(FXCACHE_PATH):
        logger.error(f"FXCACHE path does not exist: {FXCACHE_PATH}")
        return

    db_path = os.path.join(FXCACHE_PATH, '.fxcache_index.db')
    db = FxcacheDB(db_path, FXCACHE_PATH)

    # Auto-refresh: full if DB is empty, incremental if DB has a previous refresh
    if db.file_count() == 0:
        logger.info("Database is empty, starting full refresh...")
        db.refresh(mode='full')
    else:
        last = db.get_last_refresh_time()
        if last:
            logger.info(f"Database has {db.file_count()} files, starting incremental refresh...")
            db.refresh(mode='incremental')
        else:
            logger.info(f"Database has {db.file_count()} files but no refresh timestamp, starting full refresh...")
            db.refresh(mode='full')

    stats['start_time'] = datetime.now()
    print_startup_banner(args.host, args.port)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
