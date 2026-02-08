# Remote FXCACHE Server

Flask-based server for sharing FXCACHE files over the LAN. Uses SQLite for fast file existence checks.

## How It Works

**Problem**: Computer B runs FX5 fitting pipelines but has an empty/partial FXCACHE. Computer A (Mac mini) has the full FXCACHE (6.5GB, 63K files). Building cache files locally is expensive (indicators, trade simulations).

**Solution**: Run this server on Computer A. Computer B's FX5 uses `tools/remote_cache.py` to fetch missing cache files over the LAN instead of rebuilding them.

### Architecture

```
Computer A (Mac mini)                    Computer B (dev machine)
┌──────────────────────┐                ┌──────────────────────┐
│  remote-fxcache-server│  HTTP/LAN     │  FX5                 │
│  ┌────────────────┐  │◄──────────────►│  tools/remote_cache.py│
│  │ SQLite index   │  │                │  tools/cache_wrapper.py│
│  │ (63K files)    │  │                │                      │
│  └────────────────┘  │                │  constants.py        │
│  /FD_1TB_EXT/FXCACHE │                │  CACHE_BACKEND='remote'│
└──────────────────────┘                └──────────────────────┘
```

### Refresh Strategy

- **First boot** (empty DB): Full scan — walks entire FXCACHE, indexes every file. Slow over SMB (~60 min for 63K files), fast on local disk.
- **Subsequent boots**: Incremental scan — only picks up files with `mtime` newer than `last_refresh_timestamp`. Very fast (seconds if no new files).
- **Manual**: `POST /refresh?mode=full` to force a complete re-scan, or `POST /refresh?mode=incremental` for just new files.
- The SQLite DB (`.fxcache_index.db`) is stored inside the FXCACHE directory and persists across restarts.

## Setup on Mac mini

```bash
# Clone the repo
git clone git@github.com:olivier-babelcast/remote-fxcache-server.git
cd remote-fxcache-server

# Install dependencies
pip install -r requirements.txt

# Run (pointing to the local FXCACHE)
python server.py --fxcache-path /Volumes/FD_1TB_EXT/FXCACHE

# First run will do a full scan. After that, restarts do incremental (fast).
# The server prints its LAN IP at boot — use that for the client URL.
```

## Setup on Client (FX5)

```bash
# Set these env vars before running fitting pipelines
export REMOTE_FXCACHE_URL=http://<mac-mini-ip>:5002
export REMOTE_FXCACHE_MODE=cache        # 'cache' = save locally after download
export REMOTE_FXCACHE_UPLOAD=false      # 'true' to upload locally-built files back

# Activate remote backend in code (e.g., in fitting_kcbreakout.py):
#   import constants
#   if os.environ.get('REMOTE_FXCACHE_URL'):
#       constants.set_cache_backend('remote')
```

## Usage

```bash
# Default: serve local FXCACHE
python server.py

# Serve from specific path (local disk or SMB mount)
python server.py --fxcache-path /Volumes/FD_1TB_EXT/FXCACHE

# Custom port
python server.py --port 5002
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check with stats + last refresh time |
| GET | `/exists?path=<rel>` | Fast file existence check (SQLite) |
| POST | `/exists_batch` | Batch existence check (up to 500 paths) |
| GET | `/download?path=<rel>` | Download a file |
| POST | `/upload?path=<rel>` | Upload a file |
| POST | `/refresh?mode=<mode>` | Refresh index: `auto` (default), `full`, or `incremental` |
| GET | `/refresh/status` | Check refresh progress |

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `FXCACHE_PATH` | `~/GitHub/FX5/FXCACHE` | Path to FXCACHE directory |
| `FXCACHE_SERVER_PORT` | `5002` | Server port |
| `FXCACHE_SERVER_HOST` | `0.0.0.0` | Bind address |

## Client Config (FX5 side)

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `REMOTE_FXCACHE_URL` | `""` (disabled) | Server URL, e.g. `http://192.168.1.100:5002` |
| `REMOTE_FXCACHE_MODE` | `cache` | `cache` = save locally after download; `stream` = in-memory only |
| `REMOTE_FXCACHE_UPLOAD` | `false` | Upload locally-built files to server async |
