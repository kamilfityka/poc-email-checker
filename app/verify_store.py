"""
Store double opt-in: tokeny weryfikacyjne + potwierdzenia + licznik wysylek.

Dwa backendy:
  - sqlite (domyslny) - jeden plik, przetrwa restart kontenera (wazne: klient moze
    kliknac link kilka godzin pozniej). Bez zewnetrznej uslugi.
  - memory  - TTLCache w pamieci (jak w PoC), gubi tokeny po restarcie.

Wybor: VERIFY_STORE=sqlite|memory. Interfejs jest wspolny, wiec docelowe przejscie
na Redis to dopisanie trzeciego backendu bez zmian w logice double opt-in.
"""
import sqlite3
import threading
import time
from typing import Optional

from cachetools import TTLCache

from . import config


class VerifyStore:
    def save_token(self, token: str, email: str, ttl_s: int) -> None: ...
    def pop_token(self, token: str) -> Optional[str]: ...          # zuzyj jednorazowo
    def peek_token(self, token: str) -> Optional[str]: ...          # bez zuzycia
    def mark_confirmed(self, email: str) -> None: ...
    def is_confirmed(self, email: str) -> bool: ...
    def has_pending(self, email: str) -> bool: ...
    def record_send(self, email: str) -> None: ...
    def recent_send_count(self, email: str, window_s: int) -> int: ...
    def stats(self) -> dict: ...


# --- backend: memory ---------------------------------------------------------
class MemoryStore(VerifyStore):
    def __init__(self):
        self._tokens: TTLCache = TTLCache(maxsize=100_000, ttl=config.VERIFY_TTL_S)
        self._confirmed: set[str] = set()
        self._sends: list[tuple[str, float]] = []

    def save_token(self, token, email, ttl_s):
        self._tokens[token] = email

    def pop_token(self, token):
        return self._tokens.pop(token, None)

    def peek_token(self, token):
        return self._tokens.get(token)

    def mark_confirmed(self, email):
        self._confirmed.add(email)

    def is_confirmed(self, email):
        return email in self._confirmed

    def has_pending(self, email):
        return any(v == email for v in self._tokens.values())

    def record_send(self, email):
        self._sends.append((email, time.time()))

    def recent_send_count(self, email, window_s):
        now = time.time()
        self._sends = [(e, t) for e, t in self._sends if now - t < window_s]
        return sum(1 for e, t in self._sends if e == email)

    def stats(self):
        return {"backend": "memory", "pending_tokens": len(self._tokens),
                "confirmed": len(self._confirmed)}


# --- backend: sqlite ---------------------------------------------------------
class SQLiteStore(VerifyStore):
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(self._path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_db(self):
        with self._lock, self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS tokens (
                    token TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tokens_email ON tokens(email);
                CREATE TABLE IF NOT EXISTS confirmed (
                    email TEXT PRIMARY KEY,
                    confirmed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sends (
                    email TEXT NOT NULL,
                    sent_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sends_email ON sends(email);
            """)

    def _cleanup(self, c):
        now = time.time()
        c.execute("DELETE FROM tokens WHERE expires_at < ?", (now,))
        c.execute("DELETE FROM sends WHERE sent_at < ?", (now - 7 * 86400,))

    def save_token(self, token, email, ttl_s):
        now = time.time()
        with self._lock, self._conn() as c:
            self._cleanup(c)
            c.execute(
                "INSERT OR REPLACE INTO tokens(token,email,created_at,expires_at) VALUES(?,?,?,?)",
                (token, email, now, now + ttl_s),
            )

    def _get_valid(self, c, token) -> Optional[str]:
        row = c.execute(
            "SELECT email, expires_at FROM tokens WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        email, expires_at = row
        if expires_at < time.time():
            c.execute("DELETE FROM tokens WHERE token=?", (token,))
            return None
        return email

    def pop_token(self, token):
        with self._lock, self._conn() as c:
            email = self._get_valid(c, token)
            if email is not None:
                c.execute("DELETE FROM tokens WHERE token=?", (token,))
            return email

    def peek_token(self, token):
        with self._lock, self._conn() as c:
            return self._get_valid(c, token)

    def mark_confirmed(self, email):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO confirmed(email,confirmed_at) VALUES(?,?)",
                (email, time.time()),
            )

    def is_confirmed(self, email):
        with self._lock, self._conn() as c:
            return c.execute(
                "SELECT 1 FROM confirmed WHERE email=?", (email,)
            ).fetchone() is not None

    def has_pending(self, email):
        with self._lock, self._conn() as c:
            self._cleanup(c)
            return c.execute(
                "SELECT 1 FROM tokens WHERE email=? LIMIT 1", (email,)
            ).fetchone() is not None

    def record_send(self, email):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO sends(email,sent_at) VALUES(?,?)", (email, time.time()))

    def recent_send_count(self, email, window_s):
        with self._lock, self._conn() as c:
            cutoff = time.time() - window_s
            row = c.execute(
                "SELECT COUNT(*) FROM sends WHERE email=? AND sent_at>=?", (email, cutoff)
            ).fetchone()
            return row[0] if row else 0

    def stats(self):
        with self._lock, self._conn() as c:
            self._cleanup(c)
            pend = c.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            conf = c.execute("SELECT COUNT(*) FROM confirmed").fetchone()[0]
        return {"backend": "sqlite", "path": self._path,
                "pending_tokens": pend, "confirmed": conf}


# --- singleton wybrany wg konfiguracji --------------------------------------
def _build() -> VerifyStore:
    if config.VERIFY_STORE == "memory":
        return MemoryStore()
    return SQLiteStore(config.VERIFY_DB_PATH)


store: VerifyStore = _build()
