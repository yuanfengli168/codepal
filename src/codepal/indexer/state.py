"""SQLite-based file hash state tracker to avoid re-indexing unchanged files."""

from __future__ import annotations

import hashlib
import logging
import os

import aiosqlite

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS file_index (
    project TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    indexed_at INTEGER NOT NULL,
    PRIMARY KEY (project, file_path)
);
"""


class IndexState:
    """Manages file-hash tracking state in SQLite."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.expanduser(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open (or create) the SQLite database and run migrations."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute(CREATE_TABLE)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def is_changed(self, project: str, file_path: str) -> bool:
        """Return True if the file is new or its content hash has changed."""
        current_hash = _file_hash(file_path)
        if current_hash is None:
            return False  # File disappeared; skip
        stored = await self._get_hash(project, file_path)
        return stored != current_hash

    async def mark_indexed(self, project: str, file_path: str) -> None:
        """Update the stored hash to mark this file as freshly indexed."""
        import time
        current_hash = _file_hash(file_path)
        if current_hash is None:
            return
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO file_index (project, file_path, file_hash, indexed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project, file_path) DO UPDATE
                SET file_hash = excluded.file_hash,
                    indexed_at = excluded.indexed_at
            """,
            (project, file_path, current_hash, int(time.time())),
        )
        await self._db.commit()

    async def _get_hash(self, project: str, file_path: str) -> str | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT file_hash FROM file_index WHERE project=? AND file_path=?",
            (project, file_path),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


def _file_hash(file_path: str) -> str | None:
    """Return SHA256 hex digest of file contents, or None if unreadable."""
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None
