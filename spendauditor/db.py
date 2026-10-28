"""Database access. Two kinds of connection, on purpose:

- `connect_ro()` is what every agent uses. It is read-only at three levels: the file is opened in
  SQLite's read-only mode, an authorizer refuses anything but reading whitelisted tables, and a progress
  handler stops runaway queries. Even a perfectly crafted malicious query cannot write or escape.
- `connect_rw()` is used only by the loader and by the LLM gateway when it logs its own spend.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from . import config
from .config import READABLE_TABLES

QUERY_TIMEOUT_S = 5.0

# Authorizer action codes that only read (see https://www.sqlite.org/c3ref/c_alter_table.html)
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),  # WITH RECURSIVE
}


class UnsafeQuery(Exception):
    """Raised when a query tries to do something other than read whitelisted tables."""


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        # arg1 = table name. CTE names never reach here; sqlite_master etc. are refused.
        if arg1 not in READABLE_TABLES:
            return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg2 and arg2.lower() in {"load_extension", "readfile", "writefile"}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


@contextmanager
def connect_ro(path=None):
    p = path or config.DB_PATH
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.set_authorizer(_authorizer)
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        yield con
    finally:
        con.close()


@contextmanager
def connect_rw(path=None):
    con = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def query(sql: str, params: tuple | dict = (), path=None) -> list[dict]:
    """Run one read-only query and return plain dicts."""
    with connect_ro(path) as con:
        try:
            cur = con.execute(sql, params)
        except sqlite3.DatabaseError as e:
            if any(w in str(e) for w in ("not authorized", "interrupted", "prohibited")):
                raise UnsafeQuery(str(e)) from e
            raise
        return [dict(r) for r in cur.fetchall()]
