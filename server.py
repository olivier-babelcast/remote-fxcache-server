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
import logging
import os
import socket
import threading
from datetime import datetime

from flask import Flask, jsonify, request, send_file

from db import FxcacheDB

app = Flask(__name__)

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
    started = db.refresh_full()
    if not started:
        return jsonify({'status': 'already_running'}), 409
    return jsonify({'status': 'started', 'message': 'Database refresh started in background'})


@app.route('/refresh/status', methods=['GET'])
def refresh_status():
    return jsonify(db.get_refresh_status())


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
    print()


def main():
    global db, FXCACHE_PATH

    parser = argparse.ArgumentParser(description='Remote FXCACHE server')
    parser.add_argument('--port', type=int, default=int(os.environ.get('FXCACHE_SERVER_PORT', 5002)),
                        help='Port to listen on (default: 5002)')
    parser.add_argument('--host', type=str, default=os.environ.get('FXCACHE_SERVER_HOST', '0.0.0.0'),
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--fxcache-path', type=str,
                        default=os.environ.get('FXCACHE_PATH', os.path.expanduser('~/GitHub/FX5/FXCACHE')),
                        help='Path to FXCACHE directory')
    args = parser.parse_args()

    FXCACHE_PATH = os.path.realpath(args.fxcache_path)
    if not os.path.isdir(FXCACHE_PATH):
        logger.error(f"FXCACHE path does not exist: {FXCACHE_PATH}")
        return

    db_path = os.path.join(FXCACHE_PATH, '.fxcache_index.db')
    db = FxcacheDB(db_path, FXCACHE_PATH)

    # Auto-refresh if DB is empty
    if db.file_count() == 0:
        logger.info("Database is empty, starting initial refresh...")
        db.refresh_full()

    stats['start_time'] = datetime.now()
    print_startup_banner(args.host, args.port)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
