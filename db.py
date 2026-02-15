"""
LMDB wrapper for the remote FXCACHE server.

Provides direct access to the LMDB database (DARTDb) that replaced
the filesystem-based FXCACHE. No SQLite, no filesystem scanning.
"""

import logging
import os

import lmdb

logger = logging.getLogger(__name__)


class LmdbStore:
    def __init__(self, lmdb_path: str, map_size: int = 512 * 1024 * 1024 * 1024):
        self.lmdb_path = lmdb_path
        self.env = lmdb.open(
            lmdb_path,
            map_size=map_size,
            subdir=True,
            lock=True,
            readahead=False,
            meminit=False,
            max_dbs=0,
        )

    def get(self, key: str) -> bytes | None:
        with self.env.begin() as txn:
            return txn.get(key.encode('utf-8'))

    def put(self, key: str, value: bytes):
        with self.env.begin(write=True) as txn:
            txn.put(key.encode('utf-8'), value)

    def exists(self, key: str) -> bool:
        with self.env.begin() as txn:
            return txn.get(key.encode('utf-8')) is not None

    def exists_batch(self, keys: list[str]) -> dict[str, bool]:
        results = {}
        with self.env.begin() as txn:
            for key in keys:
                results[key] = txn.get(key.encode('utf-8')) is not None
        return results

    def delete(self, key: str) -> bool:
        with self.env.begin(write=True) as txn:
            return txn.delete(key.encode('utf-8'))

    def get_stats(self) -> dict:
        stat = self.env.stat()
        info = self.env.info()
        # Actual data size on disk
        data_file = os.path.join(self.lmdb_path, 'data.mdb')
        db_size_bytes = os.path.getsize(data_file) if os.path.exists(data_file) else 0
        return {
            'entries': stat['entries'],
            'map_size_gb': round(info['map_size'] / (1024 ** 3), 1),
            'db_size_mb': round(db_size_bytes / (1024 ** 2), 1),
        }

    def count_prefix(self, prefix: str) -> int:
        count = 0
        prefix_bytes = prefix.encode('utf-8')
        with self.env.begin() as txn:
            cursor = txn.cursor()
            if cursor.set_range(prefix_bytes):
                for key, _ in cursor:
                    if key.startswith(prefix_bytes):
                        count += 1
                    else:
                        break
        return count

    def close(self):
        self.env.close()
