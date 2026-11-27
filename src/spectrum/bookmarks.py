"""Persistent, shared signal bookmarks and their loopback HTTP API."""
import json
import math
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

LOW, HIGH = 70e6, 6e9
FIELDS = {"frequency_hz", "title", "modulation", "notes", "bandwidth_hz", "tag"}
MAX_BODY = 32 * 1024


def default_bookmark_path():
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return root / "usrp-spectrum-analyser/bookmarks.sqlite3"


def validate_bookmark(value, partial=False):
    if not isinstance(value, dict) or value.keys() - FIELDS:
        raise ValueError("Expected frequency_hz, title, modulation, notes, bandwidth_hz and tag fields only")
    result = dict(value)
    if not partial:
        if not {"frequency_hz", "title"} <= result.keys():
            raise ValueError("frequency_hz and title are required")
        result.setdefault("modulation", "")
        result.setdefault("notes", "")
        result.setdefault("bandwidth_hz", None)
        result.setdefault("tag", "")
    elif not result:
        raise ValueError("Supply at least one bookmark field")
    if "frequency_hz" in result:
        frequency = result["frequency_hz"]
        if (type(frequency) not in (int, float) or not LOW <= frequency <= HIGH
                or not math.isfinite(frequency)):
            raise ValueError("frequency_hz must be a finite number from 70000000 to 6000000000 Hz")
    if "bandwidth_hz" in result and result["bandwidth_hz"] is not None:
        bandwidth = result["bandwidth_hz"]
        if (type(bandwidth) not in (int, float) or not 1 <= bandwidth <= 56e6
                or not math.isfinite(bandwidth)):
            raise ValueError("bandwidth_hz must be null or a finite number from 1 to 56000000 Hz")
    for field, maximum in (("title", 120), ("modulation", 64), ("notes", 4096), ("tag", 64)):
        if field not in result:
            continue
        text = result[field]
        if not isinstance(text, str):
            raise ValueError(f"{field} must be text")
        text = text.strip() if field != "notes" else text
        if len(text) > maximum or (field == "title" and not text):
            raise ValueError(f"{field} must contain {'1 to ' if field == 'title' else 'at most '}{maximum} characters")
        # Reject lone surrogate code points: they cannot be stored as UTF-8 text.
        text.encode("utf-8")
        result[field] = text
    return result


class BookmarkStore:
    """One explicitly serialised SQLite connection, independent of acquisition."""

    def __init__(self, path):
        if str(path) != ":memory:":
            path = Path(path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute("""CREATE TABLE IF NOT EXISTS bookmarks (
                    id TEXT PRIMARY KEY, frequency_hz REAL NOT NULL, title TEXT NOT NULL,
                    modulation TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    bandwidth_hz REAL, tag TEXT NOT NULL DEFAULT '')""")
                columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(bookmarks)")}
                if "bandwidth_hz" not in columns:
                    self.connection.execute("ALTER TABLE bookmarks ADD COLUMN bandwidth_hz REAL")
                if "tag" not in columns:
                    self.connection.execute("ALTER TABLE bookmarks ADD COLUMN tag TEXT NOT NULL DEFAULT ''")
                self.connection.execute("CREATE TABLE IF NOT EXISTS bookmark_revision (value INTEGER NOT NULL)")
                self.connection.execute("INSERT INTO bookmark_revision SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM bookmark_revision)")
        except BaseException:
            self.connection.close()
            raise

    def close(self):
        with self.lock:
            self.connection.close()

    def list(self):
        with self.lock:
            records = self.connection.execute("SELECT * FROM bookmarks ORDER BY frequency_hz, title, id").fetchall()
            revision = self.connection.execute("SELECT value FROM bookmark_revision").fetchone()[0]
            return {"bookmarks": [dict(row) for row in records], "revision": revision}

    def get(self, identifier):
        with self.lock:
            row = self.connection.execute("SELECT * FROM bookmarks WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            return dict(row)

    def create(self, value):
        value = validate_bookmark(value)
        now = datetime.now(timezone.utc).isoformat()
        identifier = str(uuid4())
        with self.lock, self.connection:
            self.connection.execute("""INSERT INTO bookmarks
                (id, frequency_hz, title, modulation, notes, created_at, updated_at, bandwidth_hz, tag)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                     (identifier, value["frequency_hz"], value["title"],
                                      value["modulation"], value["notes"], now, now,
                                      value["bandwidth_hz"], value["tag"]))
            self.connection.execute("UPDATE bookmark_revision SET value = value + 1")
            return self.get(identifier)

    def update(self, identifier, value):
        changes = validate_bookmark(value, partial=True)
        with self.lock, self.connection:
            record = self.get(identifier)
            record.update(changes)
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.connection.execute("""UPDATE bookmarks SET frequency_hz = ?, title = ?,
                modulation = ?, notes = ?, bandwidth_hz = ?, tag = ?, updated_at = ? WHERE id = ?""",
                                     tuple(record[field] for field in
                                          ("frequency_hz", "title", "modulation", "notes", "bandwidth_hz", "tag", "updated_at", "id")))
            self.connection.execute("UPDATE bookmark_revision SET value = value + 1")
            return record

    def delete(self, identifier):
        with self.lock, self.connection:
            cursor = self.connection.execute("DELETE FROM bookmarks WHERE id = ?", (identifier,))
            if not cursor.rowcount:
                raise KeyError(identifier)
            self.connection.execute("UPDATE bookmark_revision SET value = value + 1")


def bookmark_focus(frequency, focus, value, bandwidth=None):
    """Choose a useful, bounded receiver view; this does not specify the RF LO."""
    if not isinstance(value, dict) or value.keys() - {"span_hz"}:
        raise ValueError("Expected an optional span_hz field")
    default = focus[1] - focus[0] if focus and focus[1] - focus[0] <= 56e6 else 2e6
    if bandwidth is not None:
        default = min(56e6, max(30000, 3 * bandwidth))
    span = value.get("span_hz", default)
    if type(span) not in (int, float) or not math.isfinite(span) or not 1 <= span <= 56e6:
        raise ValueError("span_hz must be a finite number from 1 to 56000000 Hz")
    start = max(LOW, min(HIGH - span, frequency - span / 2))
    return (start, start + span)


class BookmarkAPI:
    """Mixin for BaseHTTPRequestHandler; store and receiver_state are injected."""

    def bookmark_json(self, status, value):
        body = json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def bookmark_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("A JSON body and valid Content-Length are required") from None
        if self.headers.get_content_type() != "application/json" or not 0 < length <= MAX_BODY:
            raise ValueError(f"Expected application/json with a body of 1 to {MAX_BODY} bytes")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Incomplete JSON body")
        return json.loads(raw)

    def handle_bookmarks(self):
        path = self.path.partition("?")[0]
        if path != "/api/bookmarks" and not path.startswith("/api/bookmarks/"):
            return False
        parts = path.removeprefix("/api/bookmarks").strip("/").split("/") if path != "/api/bookmarks" else []
        method = self.command
        try:
            if not parts and method == "GET":
                self.bookmark_json(200, self.bookmark_store.list())
            elif not parts and method == "POST":
                self.bookmark_json(201, self.bookmark_store.create(self.bookmark_body()))
            elif len(parts) == 1 and method == "GET":
                self.bookmark_json(200, self.bookmark_store.get(parts[0]))
            elif len(parts) == 1 and method == "PATCH":
                self.bookmark_json(200, self.bookmark_store.update(parts[0], self.bookmark_body()))
            elif len(parts) == 1 and method == "DELETE":
                self.bookmark_store.delete(parts[0])
                self.bookmark_json(200, {"deleted": parts[0]})
            elif len(parts) == 2 and parts[1] == "tune" and method == "POST":
                value = self.bookmark_body()
                bookmark = self.bookmark_store.get(parts[0])
                receiver = self.receiver_state
                with receiver.navigation_lock:
                    focus = bookmark_focus(bookmark["frequency_hz"], receiver.focus, value,
                                           bookmark["bandwidth_hz"])
                    receiver.focus = focus
                    receiver.navigation_revision += 1
                    revision = receiver.navigation_revision
                self.bookmark_json(200, {"bookmark": bookmark, "focus": focus,
                                         "navigation_revision": revision})
            else:
                self.bookmark_json(404, {"error": "Unknown bookmark endpoint or method"})
        except KeyError:
            self.bookmark_json(404, {"error": "Bookmark not found"})
        except (ValueError, UnicodeError, OverflowError, RecursionError) as error:
            self.bookmark_json(400, {"error": str(error)})
        except sqlite3.Error:
            self.bookmark_json(503, {"error": "Bookmark storage unavailable; please retry"})
        return True

    def do_PATCH(self):
        if not self.handle_bookmarks():
            self.send_error(404)

    def do_DELETE(self):
        if not self.handle_bookmarks():
            self.send_error(404)
