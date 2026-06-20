"""Resumable ledger keyed by the content hash of an album's 3 image files.

Keyed by content (not filename) so renaming or re-importing the same three
photos still maps to the same album and is never processed twice. The hash is
order-independent across the three files: we hash each file, sort the digests,
and hash the concatenation — so the album identity is stable even if the file
ordering shifts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    album_key   TEXT PRIMARY KEY,
    status      TEXT NOT NULL,          -- high|cover|medium|guess|review|skipped|error
    release_id  INTEGER,
    title       TEXT,
    confidence  TEXT,
    signal      TEXT,
    committed   INTEGER NOT NULL DEFAULT 0,  -- 1 once actually written to Discogs
    data        TEXT,                   -- JSON blob of the full result
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def album_key(paths: tuple[Path, Path, Path]) -> str:
    """Order-independent content hash of the three image files."""
    digests = []
    for p in paths:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digests.append(h.hexdigest())
    combined = "".join(sorted(digests))
    return hashlib.sha256(combined.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    album_key: str
    status: str
    release_id: int | None
    title: str | None
    confidence: str | None
    signal: str | None
    committed: bool
    data: dict


class Ledger:
    def __init__(self, db_path: str | Path = "ledger.sqlite3") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, key: str) -> LedgerEntry | None:
        row = self._conn.execute(
            "SELECT * FROM albums WHERE album_key = ?", (key,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def is_committed(self, key: str) -> bool:
        """True only if the album was actually written to Discogs. Dry-run
        results are recorded but not committed, so a later --commit re-processes
        them."""
        row = self._conn.execute(
            "SELECT committed FROM albums WHERE album_key = ?", (key,)
        ).fetchone()
        return bool(row and row["committed"])

    def record(
        self,
        key: str,
        *,
        status: str,
        release_id: int | None,
        title: str | None,
        confidence: str | None,
        signal: str | None,
        committed: bool,
        data: dict,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO albums
                (album_key, status, release_id, title, confidence, signal, committed, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(album_key) DO UPDATE SET
                status=excluded.status,
                release_id=excluded.release_id,
                title=excluded.title,
                confidence=excluded.confidence,
                signal=excluded.signal,
                committed=excluded.committed,
                data=excluded.data
            """,
            (
                key,
                status,
                release_id,
                title,
                confidence,
                signal,
                int(committed),
                json.dumps(data),
            ),
        )
        self._conn.commit()


def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        album_key=row["album_key"],
        status=row["status"],
        release_id=row["release_id"],
        title=row["title"],
        confidence=row["confidence"],
        signal=row["signal"],
        committed=bool(row["committed"]),
        data=json.loads(row["data"]) if row["data"] else {},
    )
