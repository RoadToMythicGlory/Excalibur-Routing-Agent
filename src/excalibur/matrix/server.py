from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import ControlAction, ControlCommand
from excalibur.matrix.service import MatrixService

logger = logging.getLogger(__name__)


class MatrixHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, service: MatrixService) -> None:
        super().__init__(address, handler)
        self.service = service


class MatrixRequestHandler(BaseHTTPRequestHandler):
    """Dependency-free HTTP surface for the control plane and CS2 GSI.

    Routes:
      GET  /api/state         -> full matrix/bus/latency/story snapshot
      GET  /api/config        -> editable input/output configuration
      GET  /api/files         -> browse backend directories/media files (picker)
      POST /gsi               -> CS2 Game State Integration payload sink
      POST /api/soccer/events -> soccer structured heartbeat / event sink
      POST /api/control       -> operator command (manual cut, lock, hold, emergency)
      POST /api/config/input  -> set input metadata (identity, scene, source)
      POST /api/config/matrix -> set input/output slot counts
      POST /api/config/program-> toggle/configure the virtual-camera device
      POST /api/agent         -> activate/deactivate the AI director
    """

    server: MatrixHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature
        logger.debug("http %s", format % args)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802 - http verb
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802 - http verb
        if self.path.startswith("/api/state"):
            self._send(200, self.server.service.state_snapshot())
            return
        if self.path.startswith("/api/config"):
            self._send(200, self.server.service.config_snapshot())
            return
        if self.path.startswith("/api/preview/"):
            self._send_preview()
            return
        if self.path.startswith("/api/files"):
            self._send_files()
            return
        if self.path == "/" or self.path.startswith("/health"):
            self._send(200, {"status": "ok", "now_ms": now_ms()})
            return
        self._send(404, {"error": "not_found"})

    def _send_files(self) -> None:
        # /api/files?path=<dir or file on the backend machine>
        query = parse_qs(urlparse(self.path).query)
        raw = query.get("path", [""])[0]
        path = unquote(raw)
        try:
            listing = self.server.service.browse_media(path)
        except FileNotFoundError as error:
            self._send(404, {"error": str(error)})
            return
        self._send(200, listing)

    def _send_preview(self) -> None:
        # /api/preview/<input_id>.jpg (ignore any cache-busting query string)
        tail = self.path[len("/api/preview/") :].split("?", 1)[0]
        input_id = tail[:-4] if tail.endswith(".jpg") else tail
        image = self.server.service.preview_jpeg(input_id)
        if image is None:
            self._send(404, {"error": "no_signal", "input_id": input_id})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(image)))
        self.end_headers()
        self.wfile.write(image)

    def do_POST(self) -> None:  # noqa: N802 - http verb
        payload = self._read_json()
        if self.path.startswith("/gsi"):
            self.server.service.feed_gsi(payload)
            self._send(200, {"status": "accepted"})
            return
        if self.path.startswith("/api/soccer/events"):
            self._handle_soccer_events(payload)
            return
        if self.path.startswith("/api/control"):
            decision = self._handle_control(payload)
            self._send(200, decision)
            return
        if self.path.startswith("/api/config/matrix"):
            self._handle_matrix_config(payload)
            return
        if self.path.startswith("/api/config/input"):
            self._handle_input_config(payload)
            return
        if self.path.startswith("/api/config/program"):
            self._handle_program_config(payload)
            return
        if self.path.startswith("/api/agent"):
            self._handle_agent_toggle(payload)
            return
        if self.path.startswith("/api/sport"):
            self._handle_sport_mode(payload)
            return
        self._send(404, {"error": "not_found"})

    def _handle_soccer_events(self, payload: dict) -> None:
        try:
            snapshot = self.server.service.feed_soccer_events(payload)
        except RuntimeError as error:
            self._send(409, {"error": str(error)})
            return
        if not snapshot.pop("accepted", True):
            self._send(422, {"status": "rejected", "soccer": snapshot})
            return
        self._send(200, {"status": "accepted", "soccer": snapshot})

    def _handle_sport_mode(self, payload: dict) -> None:
        sport = payload.get("sport_mode")
        if not sport:
            self._send(400, {"error": "missing_sport_mode"})
            return
        try:
            result = self.server.service.set_sport_mode(
                str(sport), game_profile=payload.get("game_profile")
            )
        except (ValueError, FileNotFoundError) as error:
            self._send(400, {"error": str(error)})
            return
        self._send(200, {"status": "ok", **result})

    def _handle_agent_toggle(self, payload: dict) -> None:
        if "enabled" not in payload:
            self._send(400, {"error": "missing_enabled"})
            return
        enabled = self.server.service.set_agent_enabled(payload.get("enabled"))
        self._send(
            200,
            {
                "status": "ok",
                "agent_enabled": enabled,
                "director_mode": self.server.service.director_mode.value,
            },
        )

    def _handle_matrix_config(self, payload: dict) -> None:
        try:
            result = self.server.service.set_matrix_size(
                input_count=payload.get("input_count"),
                output_count=payload.get("output_count"),
            )
        except (ValueError, TypeError) as error:
            self._send(400, {"error": str(error)})
            return
        self._send(200, {"status": "ok", **result})

    def _handle_program_config(self, payload: dict) -> None:
        # Configure the Excalibur virtual-camera device output. Accepts
        # virtualcam_* fields (or a nested "virtualcam" object).
        vcam = payload.get("virtualcam")
        if not isinstance(vcam, dict):
            vcam = {
                "enabled": payload.get("virtualcam_enabled"),
                "device": payload.get("virtualcam_device"),
                "width": payload.get("virtualcam_width"),
                "height": payload.get("virtualcam_height"),
                "fps": payload.get("virtualcam_fps"),
                "manager_bin": payload.get("virtualcam_manager_bin"),
            }
        try:
            status = self.server.service.set_virtualcam_config(vcam)
        except (ValueError, TypeError) as error:
            self._send(400, {"error": str(error)})
            return
        self._send(200, {"status": "ok", "virtualcam": status})

    def _handle_input_config(self, payload: dict) -> None:
        input_id = payload.get("input_id")
        if not input_id:
            self._send(400, {"error": "missing_input_id"})
            return
        try:
            saved = self.server.service.update_input_config(input_id, payload)
        except KeyError as error:
            self._send(404, {"error": str(error)})
            return
        self._send(200, {"status": "ok", "input_id": input_id, "config": saved})

    def _handle_control(self, payload: dict) -> dict:
        try:
            action = ControlAction(payload.get("action", ""))
        except ValueError:
            return {"error": "unknown_action", "action": payload.get("action")}
        command = ControlCommand(
            action=action,
            operator=payload.get("operator", "operator"),
            target_output=payload.get("target_output", "output_01"),
            candidate_input=payload.get("candidate_input"),
            reason=payload.get("reason", ""),
            hold_ms=float(payload.get("hold_ms", 0.0)),
            lock_ms=payload.get("lock_ms"),
        )
        decision = self.server.service.routing.handle_control(command)
        return {
            "outcome": decision.outcome.value,
            "output_id": decision.output_id,
            "input_id": decision.input_id,
            "reason": decision.reason,
        }


def serve(service: MatrixService, host: str = "127.0.0.1", port: int = 8800) -> None:
    httpd = MatrixHTTPServer((host, port), MatrixRequestHandler, service)
    logger.info("Matrix control API listening on http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.info("Shutting down matrix control API")
        httpd.shutdown()
