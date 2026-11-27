"""Bookmark tests use temporary SQLite and an HTTP receiver stub, never UHD."""
import http.client
import json
import math
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookmarks import BookmarkAPI, BookmarkStore, bookmark_focus, validate_bookmark


def signal(**changes):
    return {"frequency_hz": 145.5e6, "title": "Signal candidate", "modulation": "Unknown",
            "notes": "Unverified signal observation", **changes}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "nested/bookmarks.sqlite3"
        self.store = BookmarkStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_crud_persistence_revision_and_no_html_processing(self):
        self.assertEqual(self.store.list(), {"bookmarks": [], "revision": 0})
        record = self.store.create(signal(title="  Candidate  ", notes="<script>not markup</script>\nΩ"))
        self.assertEqual(record["title"], "Candidate")
        self.assertEqual(self.store.list()["revision"], 1)
        self.assertIsNone(record["bandwidth_hz"])
        self.assertEqual(record["tag"], "")
        updated = self.store.update(record["id"], {"frequency_hz": 145.6e6, "modulation": "FM?",
                                                 "bandwidth_hz": 12500, "tag": "  Candidate  "})
        self.assertEqual(updated["bandwidth_hz"], 12500)
        self.assertEqual(updated["tag"], "Candidate")
        self.assertEqual(updated["created_at"], record["created_at"])
        self.assertEqual(updated["notes"], "<script>not markup</script>\nΩ")
        self.store.close()
        self.store = BookmarkStore(self.path)
        self.assertEqual(self.store.get(record["id"]), updated)
        self.assertEqual(self.store.list()["revision"], 2)
        self.store.delete(record["id"])
        self.assertEqual(self.store.list(), {"bookmarks": [], "revision": 3})
        with self.assertRaises(KeyError):
            self.store.delete(record["id"])
        self.assertEqual(self.store.list()["revision"], 3)

    def test_validation_rejects_bad_fields_without_writes(self):
        values = [None, [], {}, signal(frequency_hz=True), signal(frequency_hz=math.nan),
                  signal(frequency_hz=math.inf), signal(frequency_hz=69e6), signal(frequency_hz=6001e6),
                  signal(title="  "), signal(title="x" * 121), signal(notes="x" * 4097),
                  signal(modulation="x" * 65), signal(notes=None), signal(extra="unknown"),
                  signal(frequency_hz=10 ** 400), signal(bandwidth_hz=True), signal(bandwidth_hz=0),
                  signal(bandwidth_hz=56e6 + 1), signal(bandwidth_hz=math.nan),
                  signal(bandwidth_hz=math.inf), signal(bandwidth_hz=10 ** 400),
                  signal(bandwidth_hz="12500"), signal(tag="x" * 65), signal(tag=None)]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.create(value)
        self.assertEqual(self.store.list()["revision"], 0)
        for value in ({}, {"id": "bad"}, {"title": 123}):
            with self.assertRaises(ValueError):
                validate_bookmark(value, partial=True)
        for frequency in (70e6, 6e9):
            self.assertEqual(self.store.create(signal(frequency_hz=frequency))["frequency_hz"], frequency)

    def test_bandwidth_limits_and_clear_without_losing_metadata(self):
        for bandwidth in (None, 1, 56e6, 12500.5):
            with self.subTest(bandwidth=bandwidth):
                record = self.store.create(signal(bandwidth_hz=bandwidth, tag="  Field Ω  "))
                self.assertEqual(record["bandwidth_hz"], bandwidth)
                self.assertEqual(record["tag"], "Field Ω")
                updated = self.store.update(record["id"], {"bandwidth_hz": None})
                self.assertIsNone(updated["bandwidth_hz"])
                self.assertEqual(updated["tag"], "Field Ω")
                self.assertEqual(updated["notes"], record["notes"])

    def test_legacy_database_migration_preserves_records_and_revision(self):
        self.store.close()
        legacy = self.directory.name + "/legacy.sqlite3"
        with sqlite3.connect(legacy) as connection:
            connection.execute("""CREATE TABLE bookmarks (id TEXT PRIMARY KEY, frequency_hz REAL NOT NULL,
                title TEXT NOT NULL, modulation TEXT NOT NULL, notes TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("INSERT INTO bookmarks VALUES (?, ?, ?, ?, ?, ?, ?)",
                               ("original-id", 145.5e6, "Saved", "FM?", "Preserve Ω", "created", "updated"))
            connection.execute("CREATE TABLE bookmark_revision (value INTEGER NOT NULL)")
            connection.execute("INSERT INTO bookmark_revision VALUES (7)")
        connection.close()
        self.store = BookmarkStore(legacy)
        original = self.store.get("original-id")
        self.assertEqual(original, {"id": "original-id", "frequency_hz": 145.5e6, "title": "Saved",
                                   "modulation": "FM?", "notes": "Preserve Ω", "created_at": "created",
                                   "updated_at": "updated", "bandwidth_hz": None, "tag": ""})
        self.assertEqual(self.store.list()["revision"], 7)
        self.store.close()
        self.store = BookmarkStore(legacy)
        self.assertEqual(self.store.get("original-id"), original)
        self.assertEqual(self.store.list()["revision"], 7)
        self.store.create(signal(bandwidth_hz=12500, tag="Test"))
        self.assertEqual(self.store.list()["revision"], 8)

    def test_concurrent_writes_are_serialised_and_not_lost(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda index: self.store.create(signal(title=f"Signal {index}")), range(40)))
        self.assertEqual(len({record["id"] for record in records}), 40)
        self.assertEqual(self.store.list()["revision"], 40)
        self.assertEqual(len(self.store.list()["bookmarks"]), 40)

    def test_tune_range_defaults_preserves_narrow_span_and_clamps(self):
        self.assertEqual(bookmark_focus(100e6, None, {}), (99e6, 101e6))
        self.assertEqual(bookmark_focus(100e6, (200e6, 201e6), {}), (99.5e6, 100.5e6))
        self.assertEqual(bookmark_focus(70e6, None, {}), (70e6, 72e6))
        self.assertEqual(bookmark_focus(6e9, None, {}), (5998e6, 6e9))
        self.assertEqual(bookmark_focus(100e6, None, {}, 12500), (99981250, 100018750))
        self.assertEqual(bookmark_focus(100e6, None, {}, 1), (99985000, 100015000))
        self.assertEqual(bookmark_focus(100e6, None, {}, 56e6), (72e6, 128e6))
        self.assertEqual(bookmark_focus(100e6, None, {"span_hz": 2e6}, 12500), (99e6, 101e6))
        for value in ({"span_hz": 0}, {"span_hz": True}, {"span_hz": 57e6}, {"span_hz": math.nan}, []):
            with self.assertRaises(ValueError):
                bookmark_focus(100e6, None, value)


class APITests(unittest.TestCase):
    def setUp(self):
        self.store = BookmarkStore(":memory:")
        self.receiver = SimpleNamespace(focus=None, navigation_revision=0, navigation_lock=threading.Lock())

        class Handler(BookmarkAPI, BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.handle_bookmarks():
                    self.send_error(404)

            do_POST = do_GET

            def log_message(self, *_args):
                pass

        Handler.bookmark_store = self.store
        Handler.receiver_state = self.receiver
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.close()

    def request(self, method, path="/api/bookmarks", value=None, raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = raw if raw is not None else json.dumps(value).encode() if value is not None else None
        connection.request(method, path, body, headers or ({"Content-Type": "application/json"} if body else {}))
        response = connection.getresponse()
        data = json.loads(response.read())
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertTrue(response.getheader("Content-Type").startswith("application/json"))
        status = response.status
        connection.close()
        return status, data

    def test_crud_and_tune_are_separate_and_return_json(self):
        status, created = self.request("POST", value=signal())
        self.assertEqual(status, 201)
        path = "/api/bookmarks/" + created["id"]
        self.assertIsNone(self.receiver.focus)
        self.assertEqual(self.request("GET", path), (200, created))
        status, listed = self.request("GET", "/api/bookmarks?refresh=1")
        self.assertEqual((status, listed["revision"]), (200, 1))
        self.assertEqual(self.request("PATCH", path, {"notes": "Edited"})[1]["notes"], "Edited")
        self.assertIsNone(self.receiver.focus)
        status, recalled = self.request("POST", path + "/tune", {})
        self.assertEqual(status, 200)
        self.assertEqual(recalled["focus"], [144.5e6, 146.5e6])
        self.assertEqual(recalled["navigation_revision"], 1)
        self.assertEqual(list(self.receiver.focus), recalled["focus"])
        before = self.receiver.focus
        self.assertEqual(self.request("POST", path + "/tune", {"span_hz": -1})[0], 400)
        self.assertEqual(self.receiver.focus, before)
        self.assertEqual(self.receiver.navigation_revision, 1)
        self.assertEqual(self.request("DELETE", path), (200, {"deleted": created["id"]}))
        self.assertEqual(self.receiver.focus, before)
        self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("POST", path + "/tune", {})[0], 404)

    def test_invalid_json_types_limits_and_missing_records(self):
        for raw in (b"{", b"[]", b"null", b"{\"frequency_hz\":NaN,\"title\":\"bad\"}",
                    b"x" * 32769, b'"' + b"x" * 33000 + b'"'):
            with self.subTest(raw=raw[:40]):
                status, body = self.request("POST", raw=raw)
                self.assertEqual(status, 400)
                self.assertIn("error", body)
        self.assertEqual(self.request("POST", raw=b"{}", headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("PATCH", "/api/bookmarks/missing", {"title": "Name"})[0], 404)
        self.assertEqual(self.request("DELETE", "/api/bookmarks/missing")[0], 404)
        self.assertEqual(self.request("POST", "/api/bookmarks/missing/extra", {})[0], 404)
        self.assertEqual(self.store.list()["revision"], 0)

    def test_bandwidth_and_tag_http_roundtrip_and_recall(self):
        status, record = self.request("POST", value=signal(bandwidth_hz=25000, tag="  Monitor  "))
        self.assertEqual(status, 201)
        self.assertEqual((record["bandwidth_hz"], record["tag"]), (25000, "Monitor"))
        self.assertIsNone(self.receiver.focus)
        path = "/api/bookmarks/" + record["id"]
        status, recalled = self.request("POST", path + "/tune", {})
        self.assertEqual((status, recalled["focus"]), (200, [145462500, 145537500]))
        status, recalled = self.request("POST", path + "/tune", {"span_hz": 1e6})
        self.assertEqual((status, recalled["focus"]), (200, [145e6, 146e6]))
        before = self.receiver.focus
        self.assertEqual(self.request("PATCH", path, {"bandwidth_hz": 0})[0], 400)
        self.assertEqual(self.receiver.focus, before)
        status, updated = self.request("PATCH", path, {"bandwidth_hz": None, "tag": ""})
        self.assertEqual((status, updated["bandwidth_hz"], updated["tag"]), (200, None, ""))
        self.assertEqual(self.receiver.focus, before)

    def test_storage_failure_is_actionable_and_does_not_tune(self):
        self.store.close()
        status, body = self.request("POST", value=signal())
        self.assertEqual(status, 503)
        self.assertIn("retry", body["error"])
        self.assertIsNone(self.receiver.focus)
        # Restore a valid store for tearDown.
        self.store = BookmarkStore(":memory:")


if __name__ == "__main__":
    unittest.main()
