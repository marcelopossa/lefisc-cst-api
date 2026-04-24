"""
Cache persistente em SQLite com TTL.

Motivação: o TTLCache em memória perde tudo no restart do uvicorn,
invalidando até 24h de consultas e criando pressão desnecessária no Lefisc.

API compatível (get/set/contains/clear/len) com o TTLCache anterior,
mas persiste em disco. Armazena CSTResponse serializado como JSON.

Estratégia de concorrência: abre uma conexão nova por operação (SQLite
gerencia locking via WAL). Sem estado em memória, evita problemas de
thread-safety com asyncio.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from app.models import CSTResponse


class SQLiteCache:
    """Cache persistente com TTL em SQLite."""

    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self._db_path = db_path
        self._ttl = ttl_seconds
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path, timeout=5.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def _ensure_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS ix_expires ON cache(expires_at)")

    def _get_valid(self, key: str) -> Optional[str]:
        """Retorna o valor se existir e não tiver expirado; None caso contrário."""
        now = int(time.time())
        with self._conn() as c:
            row = c.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at < now:
                c.execute("DELETE FROM cache WHERE key = ?", (key,))
                return None
            return value

    def __contains__(self, key: str) -> bool:
        return self._get_valid(key) is not None

    def __getitem__(self, key: str) -> CSTResponse:
        value = self._get_valid(key)
        if value is None:
            raise KeyError(key)
        return CSTResponse.model_validate_json(value)

    def __setitem__(self, key: str, response: CSTResponse) -> None:
        payload = response.model_dump_json()
        expires_at = int(time.time()) + self._ttl
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) "
                "VALUES (?, ?, ?)",
                (key, payload, expires_at),
            )

    def __len__(self) -> int:
        now = int(time.time())
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM cache WHERE expires_at >= ?", (now,)
            ).fetchone()
            return row[0] if row else 0

    def clear(self) -> int:
        """Remove todas as entradas (inclusive as ainda válidas). Retorna quantas foram removidas."""
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) FROM cache").fetchone()
            total = row[0] if row else 0
            c.execute("DELETE FROM cache")
            return total

    def purge_expired(self) -> int:
        """Remove só as entradas expiradas. Retorna quantas foram removidas."""
        now = int(time.time())
        with self._conn() as c:
            cur = c.execute("DELETE FROM cache WHERE expires_at < ?", (now,))
            return cur.rowcount
