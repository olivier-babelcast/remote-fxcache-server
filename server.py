#!/usr/bin/env python3
"""
Remote FXCACHE server — LMDB front-end.

Serves cache entries over HTTP from an LMDB database (DARTDb).

Usage:
    python server.py
    python server.py --port 5002
    python server.py --lmdb-path /Volumes/FD_1TB_EXT/DARTDb
"""

import argparse
import json as _json
import logging
import os
import socket
from datetime import datetime

from flask import Flask, Response, jsonify, request

from db import LmdbStore

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
store: LmdbStore = None
LMDB_PATH: str = ''

# Stats
stats = {
    'gets': 0,
    'puts': 0,
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


# --- Endpoints ---

@app.route('/', methods=['GET'])
def index():
    endpoints = [
        ('GET',  '/health',         'Health check with LMDB stats'),
        ('GET',  '/exists?key=',    'Check key existence'),
        ('POST', '/exists_batch',   'Batch existence check'),
        ('GET',  '/get?key=',       'Get value by key'),
        ('POST', '/put?key=',       'Store value for key'),
        ('POST', '/debug/log',      'Post debug log from a machine'),
        ('GET',  '/debug/log',      'Get debug logs (?machine=X)'),
        ('GET',  '/debug/log/list', 'List machines with logs'),
        ('GET',  '/debug/compare',  'Compare logs from two machines'),
    ]
    rows = ''.join(
        f'<tr><td>{method}</td><td><a href="{path}">{path}</a></td><td>{desc}</td></tr>'
        for method, path, desc in endpoints
    )
    return f'''<html><head><title>Remote FXCACHE Server (LMDB)</title>
<style>body{{font-family:monospace;margin:2em}}table{{border-collapse:collapse}}
td,th{{padding:4px 12px;text-align:left;border-bottom:1px solid #ddd}}</style>
</head><body><h2>Remote FXCACHE Server (LMDB)</h2>
<table><tr><th>Method</th><th>Path</th><th>Description</th></tr>{rows}</table>
</body></html>'''


@app.route('/health', methods=['GET'])
def health():
    uptime = (datetime.now() - stats['start_time']).total_seconds() if stats['start_time'] else 0
    return jsonify({
        'status': 'ok',
        'lmdb_path': LMDB_PATH,
        'lmdb_stats': store.get_stats(),
        'uptime_seconds': round(uptime, 1),
        'stats': stats,
    })


@app.route('/exists', methods=['GET'])
def exists():
    key = request.args.get('key', '')
    if not key:
        return jsonify({'error': 'Missing key parameter'}), 400
    stats['exists_checks'] += 1
    return jsonify({'exists': store.exists(key), 'key': key})


@app.route('/exists_batch', methods=['POST'])
def exists_batch():
    data = request.get_json(silent=True)
    if not data or 'keys' not in data:
        return jsonify({'error': 'Missing keys field'}), 400
    keys = data['keys']
    if not isinstance(keys, list) or len(keys) > 10000:
        return jsonify({'error': 'keys must be a list of max 10000 items'}), 400
    stats['exists_checks'] += len(keys)
    return jsonify({'results': store.exists_batch(keys)})


@app.route('/get', methods=['GET'])
def get_value():
    key = request.args.get('key', '')
    if not key:
        return jsonify({'error': 'Missing key parameter'}), 400
    try:
        value = store.get(key)
        if value is None:
            return jsonify({'error': 'Key not found', 'key': key}), 404
        stats['gets'] += 1
        stats['bytes_sent'] += len(value)
        return Response(value, mimetype='application/octet-stream')
    except Exception as e:
        stats['errors'] += 1
        logger.error(f"Get error for {key}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/put', methods=['POST'])
def put_value():
    key = request.args.get('key', '')
    if not key:
        return jsonify({'error': 'Missing key parameter'}), 400
    data = request.get_data()
    if not data:
        return jsonify({'error': 'Empty body'}), 400
    try:
        store.put(key, data)
        size = len(data)
        stats['puts'] += 1
        stats['bytes_received'] += size
        logger.info(f"Put {key} ({size} bytes)")
        return jsonify({'status': 'ok', 'key': key, 'size': size})
    except Exception as e:
        stats['errors'] += 1
        logger.error(f"Put error for {key}: {e}")
        return jsonify({'error': str(e)}), 500


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
            d = {'key': key}
            d[name1] = str(v1)[:500] if isinstance(v1, str) and len(str(v1)) > 500 else v1
            d[name2] = str(v2)[:500] if isinstance(v2, str) and len(str(v2)) > 500 else v2
            diffs.append(d)
    return diffs


# --- Startup ---

def print_startup_banner(host: str, port: int):
    local_ip = get_local_ip()
    lmdb_stats = store.get_stats()
    print()
    print("=" * 60)
    print("Remote FXCACHE Server (LMDB)")
    print("=" * 60)
    print(f"Local IP:    {local_ip}")
    print(f"Port:        {port}")
    print(f"URL:         \033]8;;http://{local_ip}:{port}\033\\http://{local_ip}:{port}\033]8;;\033\\")
    print(f"LMDB path:   {LMDB_PATH}")
    print(f"Entries:     {lmdb_stats['entries']:,}")
    print(f"DB size:     {lmdb_stats['db_size_mb']:,.1f} MB")
    print("=" * 60)
    print()
    base = f"http://{local_ip}:{port}"
    def _link(path, label):
        return f"\033]8;;{base}{path}\033\\{label}\033]8;;\033\\"

    print("Endpoints:")
    print(f"  GET  {_link('/health', '/health'):<50s} Health check")
    print(f"  GET  {_link('/exists?key=', '/exists?key='):<50s} Check key existence")
    print(f"  POST {_link('/exists_batch', '/exists_batch'):<50s} Batch existence check")
    print(f"  GET  {_link('/get?key=', '/get?key='):<50s} Get value by key")
    print(f"  POST {_link('/put?key=', '/put?key='):<50s} Store value for key")
    print(f"  POST {_link('/debug/log', '/debug/log'):<50s} Post debug log from a machine")
    print(f"  GET  {_link('/debug/log', '/debug/log'):<50s} Get debug logs (?machine=X)")
    print(f"  GET  {_link('/debug/log/list', '/debug/log/list'):<50s} List machines with logs")
    print(f"  GET  {_link('/debug/compare', '/debug/compare'):<50s} Compare logs from two machines")
    print()


def main():
    global store, LMDB_PATH

    parser = argparse.ArgumentParser(description='Remote FXCACHE server (LMDB)')
    parser.add_argument('--port', type=int, default=int(os.environ.get('FXCACHE_SERVER_PORT', 5002)),
                        help='Port to listen on (default: 5002)')
    parser.add_argument('--host', type=str, default=os.environ.get('FXCACHE_SERVER_HOST', '0.0.0.0'),
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--lmdb-path', type=str,
                        default=os.environ.get('LMDB_PATH', '/Volumes/FD_1TB_EXT/DARTDb'),
                        help='Path to LMDB directory (default: /Volumes/FD_1TB_EXT/DARTDb)')
    args = parser.parse_args()

    LMDB_PATH = os.path.realpath(args.lmdb_path)
    if not os.path.isdir(LMDB_PATH):
        logger.error(f"LMDB path does not exist: {LMDB_PATH}")
        return

    store = LmdbStore(LMDB_PATH)

    stats['start_time'] = datetime.now()
    print_startup_banner(args.host, args.port)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
