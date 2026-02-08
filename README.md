# Remote FXCACHE Server

Flask-based server for sharing FXCACHE files over the LAN. Uses SQLite for fast file existence checks.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Default: serve local FXCACHE
python server.py

# Serve from specific path (e.g., SMB mount)
python server.py --fxcache-path /Volumes/FD_1TB_EXT/FXCACHE

# Custom port
python server.py --port 5002
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check with stats |
| GET | `/exists?path=<rel>` | Fast file existence check |
| POST | `/exists_batch` | Batch existence check (up to 500 paths) |
| GET | `/download?path=<rel>` | Download a file |
| POST | `/upload?path=<rel>` | Upload a file |
| POST | `/refresh` | Rebuild SQLite index from filesystem |
| GET | `/refresh/status` | Check refresh progress |

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `FXCACHE_PATH` | `~/GitHub/FX5/FXCACHE` | Path to FXCACHE directory |
| `FXCACHE_SERVER_PORT` | `5002` | Server port |
| `FXCACHE_SERVER_HOST` | `0.0.0.0` | Bind address |

## Client

The FX5 client is in `tools/remote_cache.py`. Configure with:

```bash
export REMOTE_FXCACHE_URL=http://192.168.1.100:5002
export REMOTE_FXCACHE_MODE=cache    # 'cache' or 'stream'
export REMOTE_FXCACHE_UPLOAD=false  # upload locally-built files to server
```
