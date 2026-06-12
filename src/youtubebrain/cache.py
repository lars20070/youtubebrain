"""Shared SQLite status-row cache used by multiple pipeline workers."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _assert_identifier(name: str) -> None:
    if not _ID_RE.fullmatch(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")


def _column_name(column_def: str) -> str:
    name = column_def.strip().split(maxsplit=1)[0]
    _assert_identifier(name)
    return name


# @lat: [[cache#StatusCache API]]
@dataclass(frozen=True)
class StatusCache:
    """Shared helper for status-based worker caches with per-table extra columns."""

    db_path: Path
    table: str
    extra_columns: tuple[str, ...] = ()

    def _extra_column_names(self) -> tuple[str, ...]:
        return tuple(_column_name(defn) for defn in self.extra_columns)

    # @lat: [[cache#Schema initialization]]
    def init_db(self) -> None:
        """Ensure parent dir, enable WAL, and create the cache table + status index."""
        _assert_identifier(self.table)
        for col in self.extra_columns:
            _column_name(col)

        extra_defs = "".join(f",\n                {col}" for col in self.extra_columns)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    video_id      TEXT PRIMARY KEY,
                    status        TEXT NOT NULL,
                    text          TEXT,
                    error_message TEXT,
                    attempts      INTEGER NOT NULL DEFAULT 0,
                    fetched_at    TIMESTAMP,
                    last_attempt  TIMESTAMP{extra_defs}
                );
                CREATE INDEX IF NOT EXISTS idx_{self.table}_status ON {self.table}(status);
                """
            )
            con.commit()
        finally:
            con.close()

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection to this cache db."""
        return sqlite3.connect(self.db_path)

    # @lat: [[cache#Enqueue]]
    def enqueue(self, video_ids: Iterable[str]) -> None:
        """Insert IDs as pending rows; duplicates and existing keys are ignored."""
        self.init_db()
        unique_ids = list(dict.fromkeys(video_ids))
        if not unique_ids:
            return
        con = self.connect()
        try:
            con.executemany(
                f"INSERT OR IGNORE INTO {self.table} (video_id, status) VALUES (?, 'pending')",
                [(vid,) for vid in unique_ids],
            )
            con.commit()
        finally:
            con.close()

    # @lat: [[cache#Read API]]
    def load_ok(self, video_ids: Iterable[str]) -> dict[str, str | None]:
        """Return text for ids in status ok; all other/missing rows map to None."""
        unique_ids = list(dict.fromkeys(video_ids))
        if not unique_ids:
            return {}
        if not self.db_path.exists():
            return dict.fromkeys(unique_ids, None)

        con = self.connect()
        try:
            cur = con.cursor()
            out: dict[str, str | None] = dict.fromkeys(unique_ids, None)
            for chunk_start in range(0, len(unique_ids), 500):
                chunk = unique_ids[chunk_start : chunk_start + 500]
                qmarks = ",".join("?" * len(chunk))
                rows = cur.execute(
                    f"SELECT video_id, text FROM {self.table} WHERE video_id IN ({qmarks}) AND status = 'ok'",
                    chunk,
                ).fetchall()
                for vid, text in rows:
                    out[str(vid)] = text
            return out
        finally:
            con.close()

    # @lat: [[cache#Retry selection]]
    def pending_ids(
        self,
        con: sqlite3.Connection,
        *,
        statuses: tuple[str, ...] = ("pending", "error"),
        max_attempts: int = 5,
    ) -> list[str]:
        """Return retryable ids ordered by attempts then random tie-break."""
        if not statuses:
            return []
        qmarks = ",".join("?" * len(statuses))
        rows = con.execute(
            f"""
            SELECT video_id FROM {self.table}
            WHERE status IN ({qmarks})
              AND attempts < ?
            ORDER BY attempts ASC, RANDOM()
            """,
            (*statuses, max_attempts),
        ).fetchall()
        return [str(row[0]) for row in rows]

    # @lat: [[cache#Retry selection]]
    def next_retryable(
        self,
        con: sqlite3.Connection,
        *,
        statuses: tuple[str, ...] = ("pending", "error"),
        max_attempts: int = 5,
    ) -> str | None:
        """Return one retryable id or None when no rows match."""
        if not statuses:
            return None
        qmarks = ",".join("?" * len(statuses))
        row = con.execute(
            f"""
            SELECT video_id FROM {self.table}
            WHERE status IN ({qmarks})
              AND attempts < ?
            ORDER BY attempts ASC, RANDOM()
            LIMIT 1
            """,
            (*statuses, max_attempts),
        ).fetchone()
        return str(row[0]) if row is not None else None

    # @lat: [[cache#Write API]]
    def record_result(
        self,
        con: sqlite3.Connection,
        video_id: str,
        status: str,
        *,
        text: str | None = None,
        error_message: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Record a resolved result row and increment attempts."""
        allowed = set(self._extra_column_names())
        extras = {} if extra is None else dict(extra)
        unknown = set(extras) - allowed
        if unknown:
            unknown_s = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown extra columns for {self.table}: {unknown_s}")

        set_parts = [
            "status=?",
            "text=?",
            "error_message=?",
            "attempts=attempts+1",
            "fetched_at=CURRENT_TIMESTAMP",
            "last_attempt=CURRENT_TIMESTAMP",
        ]
        params: list[object] = [status, text, error_message]
        for name, value in extras.items():
            set_parts.append(f"{name}=?")
            params.append(value)
        params.append(video_id)
        con.execute(
            f"UPDATE {self.table} SET {', '.join(set_parts)} WHERE video_id=?",
            params,
        )
        con.commit()

    # @lat: [[cache#Write API]]
    def record_attempt(
        self,
        con: sqlite3.Connection,
        video_id: str,
        status: str,
        error_message: str | None,
    ) -> None:
        """Record a failed attempt without touching fetched_at."""
        con.execute(
            f"""
            UPDATE {self.table} SET
                status=?,
                error_message=?,
                attempts=attempts+1,
                last_attempt=CURRENT_TIMESTAMP
            WHERE video_id=?
            """,
            (status, error_message, video_id),
        )
        con.commit()

    # @lat: [[cache#Progress counts]]
    def counts(self, con: sqlite3.Connection) -> tuple[int, int]:
        """Return (ok_count, total_count) for this table."""
        row = con.execute(
            f"SELECT COALESCE(SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END), 0), COUNT(*) FROM {self.table}",
        ).fetchone()
        if row is None:
            return (0, 0)
        return (int(row[0]), int(row[1]))
