# Remote FXCACHE Server

Flask-based HTTP server for serving cache entries from an LMDB database (DARTDb) over the LAN.

## Setup

```bash
git clone git@github.com:olivier-babelcast/remote-fxcache-server.git
cd remote-fxcache-server
pip install -r requirements.txt
```

## Usage

```bash
# Default (uses /Volumes/FD_1TB_EXT/DARTDb)
python server.py

# Custom LMDB path
python server.py --lmdb-path /path/to/DARTDb

# Custom port
python server.py --port 5002
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Index page with endpoint list |
| GET | `/health` | Health check with LMDB stats |
| GET | `/exists?key=` | Check key existence |
| POST | `/exists_batch` | Batch existence check (up to 10,000 keys) |
| GET | `/get?key=` | Get value by key |
| POST | `/put?key=` | Store value for key |
| POST | `/debug/log` | Post debug log from a machine |
| GET | `/debug/log` | Get debug logs (`?machine=X`) |
| GET | `/debug/log/list` | List machines with logs |
| GET | `/debug/compare` | Compare logs from two machines |

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `LMDB_PATH` | `/Volumes/FD_1TB_EXT/DARTDb` | Path to LMDB directory |
| `FXCACHE_SERVER_PORT` | `5002` | Server port |
| `FXCACHE_SERVER_HOST` | `0.0.0.0` | Bind address |