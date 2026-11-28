"""Loopback HTTP endpoints for receiver control and dashboard assets."""
import json
import mimetypes
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from bookmarks import BookmarkAPI
from listening import CaptureUnavailable, bookmark_tune, validate_tune
from receiver import MAX_AVERAGES
from sweep_plan import HIGH, LOW


class DashboardHandler(BookmarkAPI, BaseHTTPRequestHandler):
    """Serve the dashboard and translate HTTP requests into receiver operations."""

    def bookmark_recalled(self, bookmark):
        sweep = self.receiver_state
        preset = bookmark_tune(bookmark)
        sweep.bookmark_channel = asdict(preset)
        listen = sweep.listening.recall(preset, LOW, HIGH)
        return {"channel": sweep.bookmark_channel, "listen": listen}

    def do_GET(self):
        if self.handle_bookmarks():
            return
        path = self.path.partition("?")[0]
        if path == "/":
            self.serve_index()
        elif self.path in ("/api/sweep", "/api/sweep?survey=1"):
            self.serve_sweep()
        elif self.path == "/api/listen":
            self.reply_json(200, self.receiver_state.listening.snapshot())
        elif self.path == "/api/listen/acars":
            self.reply_json(200, self.receiver_state.listening.acars_log())
        elif self.path in ("/api/listen/audio", "/api/listen/iq"):
            self.stream_channel(iq=self.path.endswith("/iq"))
        elif self.path == "/api/gain-stages":
            self.reply("application/json", self.gain_stage_data)
        elif path.startswith("/api/detail/"):
            self.serve_detail()
        elif path.startswith("/assets/"):
            self.serve_asset(path.removeprefix("/assets/"))
        else:
            self.send_error(404)

    def do_POST(self):
        if self.handle_bookmarks():
            return
        if self.path == "/api/listen":
            self.configure_listening()
        elif self.path in ("/api/focus", "/api/antenna", "/api/gain", "/api/averages", "/api/image-reject"):
            self.configure_sweep()
        else:
            self.send_error(404)

    def serve_index(self):
        try:
            body = (self.dashboard / "index.html").read_bytes()
        except FileNotFoundError:
            self.send_error(503, "Dashboard not built. Run npm ci and npm run build.")
            return
        self.reply("text/html; charset=utf-8", body)

    def serve_sweep(self):
        sweep = self.receiver_state
        snapshot = sweep.snapshot
        frame = {key: value for key, value in snapshot.items() if key not in ("detail", "peak_detail", "image_detail")}
        frame["gain_table"] = self.gain_table
        frame["requested_gain"] = None if snapshot["demo"] else sweep.requested_gain
        frame["requested_averages"] = None if snapshot["demo"] else sweep.requested_averages
        with sweep.navigation_lock:
            frame["focus"] = sweep.focus
            frame["requested_antenna"] = sweep.requested_antenna
            frame["antenna_options"] = sweep.antenna_options
            frame["antenna_error"] = sweep.antenna_error
            frame["navigation_revision"] = sweep.navigation_revision
            frame["bookmark_channel"] = sweep.bookmark_channel
            frame["listen"] = sweep.listening.snapshot()
        frame["peaks"] = bool(snapshot["peak_detail"])
        frame["image_reject_available"] = sweep.image_reject_available
        frame["image_reject_requested"] = sweep.image_reject_requested
        frame["image_set"] = bool(snapshot["image_detail"])
        frame["survey_number"] = snapshot["number"]
        self.reply_json(200, frame)

    def stream_channel(self, iq):
        listening = self.receiver_state.listening
        request, _, _, epoch = listening.read_settings()
        if request is None or (request.mode != "iq" if iq else not request.audio_channels):
            return self.reply_json(409, {"error": "Start a channel in IQ mode" if iq else "Start a channel in an audio mode"})
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if not iq:
            self.send_header("X-Audio-Channels", str(request.audio_channels))
        self.end_headers()
        self.connection.settimeout(2)
        try:
            listening.stream(self.wfile.write, epoch, request.sample_size)
        except OSError:
            pass

    def serve_detail(self):
        sweep = self.receiver_state
        path, _, query = self.path.partition("?")
        frame = sweep.snapshot
        if path == f"/api/detail/{frame['number']}" and frame["detail"]:
            body, peaks, images = frame["detail"], frame["peak_detail"], frame["image_detail"]
        else:
            cached = next(((data, peaks, images) for number, data, peaks, images in sweep.recent_details.copy()
                           if path == f"/api/detail/{number}"), None)
            if cached is None:
                self.send_error(404)
                return
            body, peaks, images = cached
        options = query.split("&")
        if "peaks=1" in options and peaks:
            body += peaks
        if "image=1" in options and images:
            body += images
        self.reply("application/octet-stream", body)

    def serve_asset(self, name):
        if not name or name != Path(name).name or name in (".", ".."):
            self.send_error(404)
            return
        try:
            body = (self.dashboard / "assets" / name).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            self.send_error(404)
            return
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.reply(kind, body)

    def configure_listening(self):
        sweep = self.receiver_state
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 512:
                raise ValueError("Expected a JSON channel selection or null")
            value = json.loads(self.rfile.read(length))
            full = value == {"stop": "full"}
            request = validate_tune(value, LOW, HIGH) if value is not None and not full else None
        except (ValueError, UnicodeError) as error:
            return self.reply_json(400, {"error": str(error)})
        if request and not sweep.listening.snapshot()["available"]:
            return self.reply_json(503, {"error": "Install SciPy on the receiver to enable channel demodulation"})
        with sweep.navigation_lock:
            if full:
                sweep.focus = None
            try:
                state = sweep.listening.configure(request, sweep.focus)
            except CaptureUnavailable as error:
                return self.reply_json(409, {"error": str(error)})
            state["survey_focus"] = sweep.focus
        return self.reply_json(200, state)

    def configure_sweep(self):
        sweep = self.receiver_state
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 128:
                raise ValueError
            value = json.loads(self.rfile.read(length))
            if self.path == "/api/antenna":
                with sweep.navigation_lock:
                    if not isinstance(value, str) or value not in sweep.antenna_options:
                        raise ValueError
                    sweep.requested_antenna = value
                    sweep.antenna_error = None
            elif self.path == "/api/image-reject":
                if not sweep.image_reject_available or type(value) is not bool:
                    raise ValueError
                sweep.image_reject_requested = value
            elif self.path == "/api/gain":
                if sweep.snapshot["demo"] or type(value) not in (int, float) or not 0 <= value <= 76:
                    raise ValueError
                sweep.requested_gain = value
            elif self.path == "/api/averages":
                if sweep.snapshot["demo"] or type(value) is not int or not 1 <= value <= MAX_AVERAGES:
                    raise ValueError
                sweep.requested_averages = value
            else:
                if value is not None:
                    if (not isinstance(value, list) or len(value) != 2
                            or any(type(edge) not in (int, float) for edge in value)
                            or not LOW <= value[0] < value[1] <= HIGH):
                        raise ValueError
                    value = tuple(value)
                with sweep.navigation_lock:
                    sweep.focus = value
        except (ValueError, UnicodeError):
            messages = {
                "/api/antenna": "Expected one of the live receiver's antenna options",
                "/api/gain": "Expected live RX gain from 0 to 76 dB",
                "/api/averages": f"Expected an integer from 1 to {MAX_AVERAGES} on a live receiver",
                "/api/focus": "Expected a frequency range in Hz or null",
                "/api/image-reject": "Image rejection requires a live paired sweep at 56 MS/s and a boolean value",
            }
            self.send_error(400, messages[self.path])
            return
        self.reply_json(200, value)

    def reply_json(self, status, value):
        self.reply("application/json", json.dumps(value, allow_nan=False).encode(), status)

    def reply(self, kind, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass
