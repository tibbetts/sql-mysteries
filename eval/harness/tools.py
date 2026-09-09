"""Read-only SQL tool exposed to the model."""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|PRAGMA\s+(table_info|table_list|index_list|index_info|foreign_key_list))\b", re.I)
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|REPLACE|VACUUM|REINDEX)\b", re.I)


class SqlTool:
    def __init__(self, db_path: Path, max_rows: int = 200, max_chars: int = 20_000, timeout_s: float = 30.0):
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        self.max_rows = max_rows
        self.max_chars = max_chars
        self.timeout_s = timeout_s
        self.query_count = 0
        self.error_count = 0

    def run(self, query: str) -> str:
        if not _ALLOWED_START.match(query or "") or _FORBIDDEN.search(query):
            self.error_count += 1
            return "Error: only read-only SELECT / WITH / EXPLAIN / PRAGMA table_info queries are allowed."
        self.query_count += 1
        timer = threading.Timer(self.timeout_s, self.conn.interrupt)
        timer.start()
        try:
            cur = self.conn.execute(query)
            rows = cur.fetchmany(self.max_rows + 1)
            headers = [d[0] for d in cur.description] if cur.description else []
        except sqlite3.OperationalError as e:
            self.error_count += 1
            if "interrupted" in str(e):
                return f"Error: query exceeded {self.timeout_s:.0f}s and was cancelled."
            return f"Error: {e}"
        except sqlite3.Error as e:
            self.error_count += 1
            return f"Error: {e}"
        finally:
            timer.cancel()
        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        lines = ["|".join(headers)] + ["|".join("" if v is None else str(v) for v in r) for r in rows]
        out = "\n".join(lines)
        if len(out) > self.max_chars:
            out = out[: self.max_chars] + "\n... (output truncated at character limit)"
        if truncated:
            out += f"\n... (truncated at {self.max_rows} rows; add filters or LIMIT/OFFSET)"
        if not rows:
            out += "\n(0 rows)"
        return out

    def close(self) -> None:
        self.conn.close()
