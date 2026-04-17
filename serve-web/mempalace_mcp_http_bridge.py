#!/usr/bin/env python3

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class BridgeSession:
    def __init__(self, command: list[str]):
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._lock = threading.Lock()
        self.last_used = time.time()

    def request(self, payload: dict):
        with self._lock:
            if self._process.poll() is not None or self._process.stdin is None or self._process.stdout is None:
                raise RuntimeError("stdio MCP process is not running")

            self.last_used = time.time()
            self._process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._process.stdin.flush()

            if "id" not in payload:
                return None

            expected_id = payload["id"]

            while True:
                line = self._process.stdout.readline()
                if line == "":
                    raise RuntimeError("stdio MCP process closed stdout")

                line = line.strip()
                if not line:
                    continue

                message = json.loads(line)
                if message.get("id") == expected_id:
                    self.last_used = time.time()
                    return message

    def close(self):
        with self._lock:
            process = self._process
            if process.poll() is not None:
                return

            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass

            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class BridgeState:
    def __init__(self, command: list[str], idle_timeout_seconds: int):
        self.command = command
        self.idle_timeout_seconds = idle_timeout_seconds
        self.sessions: dict[str, BridgeSession] = {}
        self.lock = threading.Lock()

    def create_session(self) -> tuple[str, BridgeSession]:
        session_id = uuid.uuid4().hex
        session = BridgeSession(self.command)
        with self.lock:
            self.sessions[session_id] = session
        return session_id, session

    def get_session(self, session_id: str):
        with self.lock:
            return self.sessions.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def cleanup_idle_sessions(self):
        cutoff = time.time() - self.idle_timeout_seconds
        stale_ids: list[str] = []
        with self.lock:
            for session_id, session in self.sessions.items():
                if session.last_used < cutoff:
                    stale_ids.append(session_id)
        for session_id in stale_ids:
            self.delete_session(session_id)

    def close_all(self):
        with self.lock:
            session_ids = list(self.sessions.keys())
        for session_id in session_ids:
            self.delete_session(session_id)


class MemPalaceBridgeHandler(BaseHTTPRequestHandler):
    server_version = "MemPalaceMcpBridge/0.1"

    def log_message(self, format: str, *args):
        sys.stderr.write("[mempalace-mcp-bridge] " + (format % args) + "\n")

    @property
    def state(self) -> BridgeState:
        return self.server.bridge_state  # type: ignore[attr-defined]

    def _send_headers(self, status: int, *, content_type: str | None = None, session_id: str | None = None):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "OPTIONS, POST, DELETE, GET")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Mcp-Session-Id")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        self.send_header("Cache-Control", "no-store")
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if session_id is not None:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()

    def _send_json(self, status: int, payload: dict, *, session_id: str | None = None):
        body = json.dumps(payload).encode("utf-8")
        self._send_headers(status, content_type="application/json", session_id=session_id)
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_headers(204)

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        if self.path != "/mcp":
            self._send_json(404, {"error": "not found"})
            return

        session_id = self.headers.get("Mcp-Session-Id")
        if not session_id:
            self._send_json(400, {"error": "missing Mcp-Session-Id header"})
            return

        if self.state.delete_session(session_id):
            self._send_headers(204)
        else:
            self._send_json(404, {"error": "unknown MCP session"})

    def do_POST(self):
        if self.path != "/mcp":
            self._send_json(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc.msg}"})
            return

        if not isinstance(payload, dict):
            self._send_json(400, {"error": "only single JSON-RPC requests are supported"})
            return

        session_id = self.headers.get("Mcp-Session-Id")
        new_session_id = None

        if session_id:
            session = self.state.get_session(session_id)
            if session is None:
                self._send_json(404, {"error": "unknown MCP session"})
                return
        else:
            new_session_id, session = self.state.create_session()
            session_id = new_session_id

        try:
            response = session.request(payload)
        except Exception as exc:
            if new_session_id is not None:
                self.state.delete_session(new_session_id)
            self._send_json(502, {"error": str(exc)})
            return

        if response is None:
            self._send_headers(202, session_id=session_id)
            return

        self._send_json(200, response, session_id=session_id)


def start_cleanup_thread(state: BridgeState):
    def cleanup_loop():
        while True:
            time.sleep(300)
            state.cleanup_idle_sessions()

    thread = threading.Thread(target=cleanup_loop, name="mcp-bridge-cleanup", daemon=True)
    thread.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge a stdio MCP server to streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3940)
    parser.add_argument("--palace", required=True)
    parser.add_argument("--python", default="python3")
    parser.add_argument("--idle-timeout-seconds", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    palace_path = str(Path(args.palace).expanduser().resolve())
    command = [args.python, "-m", "mempalace.mcp_server", "--palace", palace_path]

    state = BridgeState(command=command, idle_timeout_seconds=args.idle_timeout_seconds)
    start_cleanup_thread(state)

    httpd = ThreadingHTTPServer((args.host, args.port), MemPalaceBridgeHandler)
    httpd.bridge_state = state  # type: ignore[attr-defined]

    def handle_signal(signum, frame):
        del signum, frame
        httpd.shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        httpd.serve_forever()
    finally:
        state.close_all()
        httpd.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())