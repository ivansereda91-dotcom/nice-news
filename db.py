"""Хранилище бота новостей: что уже отправлено, кеш сводок."""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
DB_PATH = os.getenv("DB_PATH", "news.db")


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


class _ClosingConnection(sqlite3.Connection):
    """`with connect() as c:` — коммит и сразу закрытие соединения."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS digests (kind TEXT, day TEXT, data TEXT, PRIMARY KEY (kind, day));
        CREATE TABLE IF NOT EXISTS sent (kind TEXT, day TEXT, PRIMARY KEY (kind, day));
        CREATE TABLE IF NOT EXISTS seen (title TEXT, day TEXT);
        """)


def get_digest(kind: str, day: date) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT data FROM digests WHERE kind=? AND day=?", (kind, day.isoformat())).fetchone()
    return json.loads(r["data"]) if r else None


def save_digest(kind: str, day: date, data: dict) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO digests VALUES (?,?,?)",
                  (kind, day.isoformat(), json.dumps(data, ensure_ascii=False, default=str)))
        c.execute("DELETE FROM digests WHERE day < ?", ((day - timedelta(days=30)).isoformat(),))


def mark_sent(kind: str, day: date) -> bool:
    """True, если отметили впервые (значит, надо отправлять)."""
    with connect() as c:
        cur = c.execute("INSERT OR IGNORE INTO sent VALUES (?,?)", (kind, day.isoformat()))
        return cur.rowcount > 0


def unmark_sent(kind: str, day: date) -> None:
    with connect() as c:
        c.execute("DELETE FROM sent WHERE kind=? AND day=?", (kind, day.isoformat()))


def add_seen(titles: list[str], day: date) -> None:
    with connect() as c:
        c.executemany("INSERT INTO seen VALUES (?,?)", [(t, day.isoformat()) for t in titles])
        c.execute("DELETE FROM seen WHERE day < ?", ((day - timedelta(days=5)).isoformat(),))


def seen_titles() -> list[str]:
    with connect() as c:
        return [r["title"] for r in c.execute("SELECT title FROM seen ORDER BY rowid")]
