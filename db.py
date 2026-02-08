"""
SQLite index for FXCACHE files.

Provides fast existence checks and file metadata lookups
without scanning the filesystem on every request.
"""

import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)


class FxcacheDB:
    def __init__(self, db_path: str, fxcache_path: str):
        self.db_path = db_path
        self.fxcache_path = fxcache_path
        self._write_lock = threading.Lock()
        self._refresh_running = False
        self._refresh_status = {'status': 'idle'}
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def exists(self, path: str) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT size, mtime FROM files WHERE path = ?", (path,)
            ).fetchone()
            if row:
                return {'exists': True, 'size': row['size'], 'mtime': row['mtime']}
            return {'exists': False}
        finally:
            conn.close()

    def exists_batch(self, paths: list) -> dict:
        conn = self._get_conn()
        try:
            results = {}
            # Process in chunks to avoid SQLite variable limit
            chunk_size = 500
            for i in range(0, len(paths), chunk_size):
                chunk = paths[i:i + chunk_size]
                placeholders = ','.join('?' * len(chunk))
                rows = conn.execute(
                    f"SELECT path, size FROM files WHERE path IN ({placeholders})",
                    chunk
                ).fetchall()
                found = {row['path']: {'exists': True, 'size': row['size']} for row in rows}
                for p in chunk:
                    results[p] = found.get(p, {'exists': False})
            return results
        finally:
            conn.close()

    def upsert(self, path: str, size: int, mtime: float):
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO files (path, size, mtime) VALUES (?, ?, ?)",
                    (path, size, mtime)
                )
                conn.commit()
            finally:
                conn.close()

    def file_count(self) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM files").fetchone()
            return row['cnt']
        finally:
            conn.close()

    def refresh_full(self):
        if self._refresh_running:
            return False
        self._refresh_running = True
        self._refresh_status = {
            'status': 'running',
            'files_scanned': 0,
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        t = threading.Thread(target=self._refresh_worker, daemon=True)
        t.start()
        return True

    def _refresh_worker(self):
        start = time.time()
        scanned = 0
        found_paths = set()
        try:
            conn = self._get_conn()
            batch = []
            for dirpath, _dirnames, filenames in os.walk(self.fxcache_path):
                for fname in filenames:
                    if fname.startswith('.'):
                        continue
                    full_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(full_path, self.fxcache_path)
                    try:
                        st = os.stat(full_path)
                        batch.append((rel_path, st.st_size, st.st_mtime))
                        found_paths.add(rel_path)
                    except OSError:
                        continue
                    scanned += 1
                    if scanned % 1000 == 0:
                        self._refresh_status['files_scanned'] = scanned
                        # Flush batch
                        with self._write_lock:
                            conn.executemany(
                                "INSERT OR REPLACE INTO files (path, size, mtime) VALUES (?, ?, ?)",
                                batch
                            )
                            conn.commit()
                        batch = []

            # Flush remaining
            if batch:
                with self._write_lock:
                    conn.executemany(
                        "INSERT OR REPLACE INTO files (path, size, mtime) VALUES (?, ?, ?)",
                        batch
                    )
                    conn.commit()

            # Remove stale entries
            existing = conn.execute("SELECT path FROM files").fetchall()
            stale = [row['path'] for row in existing if row['path'] not in found_paths]
            if stale:
                with self._write_lock:
                    for i in range(0, len(stale), 500):
                        chunk = stale[i:i + 500]
                        placeholders = ','.join('?' * len(chunk))
                        conn.execute(f"DELETE FROM files WHERE path IN ({placeholders})", chunk)
                    conn.commit()

            # Update meta
            with self._write_lock:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ('last_full_refresh', time.strftime('%Y-%m-%dT%H:%M:%S'))
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ('file_count', str(scanned))
                )
                conn.commit()

            conn.close()
            duration = time.time() - start
            self._refresh_status = {
                'status': 'complete',
                'total_files': scanned,
                'stale_removed': len(stale),
                'duration_seconds': round(duration, 1),
            }
            logger.info(f"Database refresh complete: {scanned} files indexed, {len(stale)} stale removed in {duration:.1f}s")

        except Exception as e:
            logger.error(f"Database refresh failed: {e}")
            self._refresh_status = {'status': 'error', 'error': str(e)}
        finally:
            self._refresh_running = False

    def get_refresh_status(self) -> dict:
        return dict(self._refresh_status)
