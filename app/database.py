"""SQLite state: per-source cursor, processed posts and failure counters."""
import sqlite3
from pathlib import Path


class StateDB:

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(self.path))

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_state (
                source TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS processed (
                source TEXT NOT NULL,
                message_key TEXT NOT NULL,
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(source, message_key)
            );

            CREATE TABLE IF NOT EXISTS failures (
                source TEXT NOT NULL,
                message_key TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(source, message_key)
            );
        """)
        self.conn.commit()

    # ---- cursor ----
    def has_cursor(self, source):
        row = self.conn.execute(
            "SELECT 1 FROM source_state WHERE source=?", (source,)
        ).fetchone()
        return row is not None

    def cursor(self, source):
        row = self.conn.execute(
            "SELECT last_message_id FROM source_state WHERE source=?",
            (source,),
        ).fetchone()
        return int(row[0]) if row else 0

    def set_cursor(self, source, message_id):
        self.conn.execute(
            """
            INSERT INTO source_state(source, last_message_id)
            VALUES(?, ?)
            ON CONFLICT(source) DO UPDATE SET last_message_id =
                MAX(last_message_id, excluded.last_message_id)
            """,
            (source, int(message_id)),
        )
        self.conn.commit()

    # ---- processed ----
    def is_processed(self, source, key):
        row = self.conn.execute(
            "SELECT 1 FROM processed WHERE source=? AND message_key=?",
            (source, str(key)),
        ).fetchone()
        return row is not None

    def mark_processed(self, source, key):
        self.conn.execute(
            "INSERT OR IGNORE INTO processed(source, message_key) VALUES(?, ?)",
            (source, str(key)),
        )
        self.conn.commit()

    # ---- failures ----
    def add_failure(self, source, key):
        self.conn.execute(
            """
            INSERT INTO failures(source, message_key, count) VALUES(?, ?, 1)
            ON CONFLICT(source, message_key) DO UPDATE SET count = count + 1
            """,
            (source, str(key)),
        )
        self.conn.commit()

        row = self.conn.execute(
            "SELECT count FROM failures WHERE source=? AND message_key=?",
            (source, str(key)),
        ).fetchone()
        return int(row[0])

    def clear_failure(self, source, key):
        self.conn.execute(
            "DELETE FROM failures WHERE source=? AND message_key=?",
            (source, str(key)),
        )
        self.conn.commit()

    # ---- maintenance ----
    def prune(self, keep_days):
        self.conn.execute(
            "DELETE FROM processed "
            "WHERE processed_at < datetime('now', ?)",
            (f"-{int(keep_days)} days",),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
