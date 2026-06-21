from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from obs_auto_director.matrix.clock import now_ms
from obs_auto_director.matrix.domain import ControlAction, ControlCommand
from obs_auto_director.matrix.service import MatrixService

logger = logging.getLogger(__name__)


class MatrixHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler, service: MatrixService) -> None:
        super().__init__(address, handler)
        self.service = service


class MatrixRequestHandler(BaseHTTPRequestHandler):
    """Dependency-free HTTP surface for the control plane and CS2 GSI.

    Routes:
      GET  /api/state    -> full matrix/bus/latency/story snapshot
      POST /gsi          -> CS2 Game State Integration payload sink
      POST /api/control  -> operator command (manual cut, lock, hold, emergency)
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
        if self.path == "/" or self.path.startswith("/health"):
            self._send(200, {"status": "ok", "now_ms": now_ms()})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - http verb
        payload = self._read_json()
        if self.path.startswith("/gsi"):
            self.server.service.feed_gsi(payload)
            self._send(200, {"status": "accepted"})
            return
        if self.path.startswith("/api/control"):
            decision = self._handle_control(payload)
            self._send(200, decision)
            return
        self._send(404, {"error": "not_found"})

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
