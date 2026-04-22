#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mempalace.palace import (
    NORMALIZE_VERSION,
    build_closet_lines,
    get_closets_collection as palace_get_closets_collection,
    get_collection as palace_get_collection,
    purge_file_closets,
    upsert_closet_lines,
)

FULL_TRANSCRIPT_ROOM = "chat_transcript_full"
FULL_TRANSCRIPT_EXTRACT_MODE = "verbatim_full"
DEFAULT_MINE_EXTRACT_MODE = "exchange"
DEFAULT_HOOK_CACHE_ROOT = "~/.mempalace-cache/copilot-hooks-bridge"


def bridge_warn(message: str) -> None:
    sys.stderr.write(f"[mempalace-mcp-bridge] {message}\n")


def sanitize_path_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return cleaned or fallback


def get_drawers_collection(palace_path: Path):
    palace_path.mkdir(parents=True, exist_ok=True)
    return palace_get_collection(str(palace_path), collection_name="mempalace_drawers", create=True)


def get_closets_collection(palace_path: Path):
    palace_path.mkdir(parents=True, exist_ok=True)
    return palace_get_closets_collection(str(palace_path), create=True)


def transcript_drawer_id(wing: str, source_file: str) -> str:
    source_hash = hashlib.sha256(source_file.encode(), usedforsecurity=False).hexdigest()[:24]
    return f"drawer_{wing}_{FULL_TRANSCRIPT_ROOM}_{source_hash}"


def transcript_closet_id_base(wing: str, source_file: str) -> str:
    source_hash = hashlib.sha256(source_file.encode(), usedforsecurity=False).hexdigest()[:24]
    return f"closet_{wing}_{FULL_TRANSCRIPT_ROOM}_{source_hash}"


def cleanup_transcript_artifacts(drawers_col, closets_col, source_file: str, warnings: list[str]) -> None:
    try:
        drawers_col.delete(where={"source_file": source_file})
    except Exception as exc:
        warnings.append(f"could not delete existing drawers for {source_file}: {exc}")

    try:
        purge_file_closets(closets_col, source_file)
    except Exception as exc:
        warnings.append(f"could not delete existing closets for {source_file}: {exc}")


def file_transcript_drawer(
    drawers_col,
    wing: str,
    source_file: str,
    session_title: str,
    transcript_document: str,
    added_by: str,
    extract_mode: str,
    client_full_export_path: str,
    warnings: list[str],
) -> str | None:
    drawer_id = transcript_drawer_id(wing, source_file)
    try:
        drawers_col.upsert(
            ids=[drawer_id],
            documents=[transcript_document],
            metadatas=[
                {
                    "wing": wing,
                    "room": FULL_TRANSCRIPT_ROOM,
                    "hall": "general",
                    "source_file": source_file,
                    "origin_source_file": client_full_export_path,
                    "chunk_index": 0,
                    "added_by": added_by,
                    "filed_at": datetime.now().isoformat(),
                    "title": session_title,
                    "record_class": "explicit_fallback_migration_futureproof_longform_rebuildable",
                    "ingest_mode": "hook_transcript_full",
                    "extract_mode": extract_mode,
                    "normalize_version": NORMALIZE_VERSION,
                }
            ],
        )
        return drawer_id
    except Exception as exc:
        warnings.append(f"could not file transcript drawer for {source_file}: {exc}")
        return None


def file_transcript_closets(
    closets_col,
    wing: str,
    source_file: str,
    drawer_id: str,
    transcript_text: str,
    added_by: str,
    extract_mode: str,
    warnings: list[str],
) -> bool:
    try:
        lines = build_closet_lines(source_file, [drawer_id], transcript_text, wing, FULL_TRANSCRIPT_ROOM)
        upsert_closet_lines(
            closets_col,
            transcript_closet_id_base(wing, source_file),
            lines,
            {
                "wing": wing,
                "room": FULL_TRANSCRIPT_ROOM,
                "hall": "general",
                "source_file": source_file,
                "added_by": added_by,
                "filed_at": datetime.now().isoformat(),
                "record_class": "explicit_fallback_migration_futureproof_longform_rebuildable",
                "ingest_mode": "hook_transcript_full",
                "extract_mode": extract_mode,
                "normalize_version": NORMALIZE_VERSION,
            },
        )
        return True
    except Exception as exc:
        warnings.append(f"could not file transcript closets for {source_file}: {exc}")
        return False


def run_mine_command(
    export_dir: Path,
    wing: str,
    palace_path: Path,
    added_by: str,
    extract_mode: str,
    timeout_seconds: int,
    warnings: list[str],
) -> bool:
    command = [
        sys.executable,
        "-m",
        "mempalace.cli",
        "--palace",
        str(palace_path),
        "mine",
        str(export_dir),
        "--mode",
        "convos",
        "--wing",
        wing,
        "--extract",
        extract_mode,
        "--agent",
        added_by,
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        stderr = result.stderr.strip()
        if stderr:
            warnings.append(stderr)
        return True
    except subprocess.TimeoutExpired as exc:
        warnings.append(f"mempalace mine timed out for {export_dir}: {exc}")
        return False
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        warnings.append(f"mempalace mine failed for {export_dir}: {details}")
        return False
    except Exception as exc:
        warnings.append(f"unexpected mine failure for {export_dir}: {exc}")
        return False


def process_copilot_hook_payload(state: "BridgeState", payload: dict) -> dict:
    required_fields = [
        "wing",
        "session_folder",
        "session_title",
        "transcript_text",
        "transcript_document",
    ]
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        return {"ok": False, "error": f"missing required field(s): {', '.join(missing)}"}

    wing = sanitize_path_component(str(payload["wing"]), "workspace")
    session_folder = sanitize_path_component(str(payload["session_folder"]), "session")
    transcript_text = str(payload["transcript_text"])
    transcript_document = str(payload["transcript_document"])
    session_title = str(payload["session_title"])
    added_by = str(payload.get("added_by") or "GitHub Copilot Hook")
    mine_extract_mode = str(payload.get("mine_extract_mode") or DEFAULT_MINE_EXTRACT_MODE)
    full_extract_mode = str(payload.get("full_transcript_extract_mode") or FULL_TRANSCRIPT_EXTRACT_MODE)
    client_full_export_path = str(payload.get("client_full_export_path") or "")

    session_dir = state.hook_cache_root / wing / session_folder
    session_dir.mkdir(parents=True, exist_ok=True)

    export_path = session_dir / "transcript.txt"
    full_export_path = session_dir / str(payload.get("full_transcript_filename") or "transcript.full.raw")
    export_path.write_text(transcript_text.rstrip() + "\n", encoding="utf-8")
    full_export_path.write_text(transcript_document.rstrip() + "\n", encoding="utf-8")

    warnings: list[str] = []
    try:
        drawers_col = get_drawers_collection(state.palace_path)
        closets_col = get_closets_collection(state.palace_path)
    except Exception as exc:
        return {"ok": False, "error": f"could not open MemPalace collections: {exc}"}

    cleanup_transcript_artifacts(drawers_col, closets_col, str(full_export_path), warnings)
    drawer_id = file_transcript_drawer(
        drawers_col=drawers_col,
        wing=wing,
        source_file=str(full_export_path),
        session_title=session_title,
        transcript_document=transcript_document,
        added_by=added_by,
        extract_mode=full_extract_mode,
        client_full_export_path=client_full_export_path,
        warnings=warnings,
    )
    closets_saved = False
    if drawer_id is not None:
        closets_saved = file_transcript_closets(
            closets_col=closets_col,
            wing=wing,
            source_file=str(full_export_path),
            drawer_id=drawer_id,
            transcript_text=transcript_text,
            added_by=added_by,
            extract_mode=full_extract_mode,
            warnings=warnings,
        )

    mine_ok = run_mine_command(
        export_dir=export_path.parent,
        wing=wing,
        palace_path=state.palace_path,
        added_by=added_by,
        extract_mode=mine_extract_mode,
        timeout_seconds=state.mine_timeout_seconds,
        warnings=warnings,
    )

    ok = drawer_id is not None and mine_ok
    return {
        "ok": ok,
        "drawer_saved": drawer_id is not None,
        "closets_saved": closets_saved,
        "mine_ok": mine_ok,
        "remote_export_path": str(export_path),
        "remote_full_export_path": str(full_export_path),
        "drawer_id": drawer_id,
        "warnings": warnings,
        "error": None if ok else "bridge ingest did not complete all MemPalace steps",
    }


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
    def __init__(
        self,
        command: list[str],
        idle_timeout_seconds: int,
        palace_path: Path,
        hook_cache_root: Path,
        mine_timeout_seconds: int,
    ):
        self.command = command
        self.idle_timeout_seconds = idle_timeout_seconds
        self.palace_path = palace_path
        self.hook_cache_root = hook_cache_root
        self.mine_timeout_seconds = mine_timeout_seconds
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
    server_version = "MemPalaceMcpBridge/0.2"

    def log_message(self, format: str, *args):
        bridge_warn(format % args)

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

    def _read_json_body(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return None

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc.msg}"})
            return None

        if not isinstance(payload, dict):
            self._send_json(400, {"error": "only single JSON object bodies are supported"})
            return None

        return payload

    def do_OPTIONS(self):
        self._send_headers(204)

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "palace_path": str(self.state.palace_path),
                    "hook_cache_root": str(self.state.hook_cache_root),
                },
            )
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
        if self.path == "/copilot-hook":
            payload = self._read_json_body()
            if payload is None:
                return
            result = process_copilot_hook_payload(self.state, payload)
            status = 200 if result.get("ok") else 502
            self._send_json(status, result)
            return

        if self.path != "/mcp":
            self._send_json(404, {"error": "not found"})
            return

        payload = self._read_json_body()
        if payload is None:
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
    parser.add_argument("--hook-cache-root", default=DEFAULT_HOOK_CACHE_ROOT)
    parser.add_argument("--mine-timeout-seconds", type=int, default=40)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    palace_path = Path(args.palace).expanduser().resolve()
    hook_cache_root = Path(args.hook_cache_root).expanduser().resolve()
    hook_cache_root.mkdir(parents=True, exist_ok=True)

    command = [args.python, "-m", "mempalace.mcp_server", "--palace", str(palace_path)]

    state = BridgeState(
        command=command,
        idle_timeout_seconds=args.idle_timeout_seconds,
        palace_path=palace_path,
        hook_cache_root=hook_cache_root,
        mine_timeout_seconds=args.mine_timeout_seconds,
    )
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