#!/usr/bin/env python3

import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from urllib import error, parse, request

ADDED_BY = "GitHub Copilot Hook"
MINE_EVENTS = {"UserPromptSubmit", "PreCompact", "Stop"}
EXPORTS_DIRNAME = "exports"
FULL_TRANSCRIPT_ROOM = "chat_transcript_full"
FULL_TRANSCRIPT_EXTRACT_MODE = "verbatim_full"
MINE_EXTRACT_MODE = "exchange"
FULL_TRANSCRIPT_FILENAME = "transcript.full.raw"
KNOWN_RECORD_TYPES = {"session.start", "user.message", "assistant.message"}
DEFAULT_BRIDGE_URL = "http://10.0.0.12:3940"
DEFAULT_BRIDGE_ENDPOINT = "/copilot-hook"
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 8.0


def warn(message: str) -> None:
    print(f"[mempalace-hook] {message}", file=sys.stderr)


def get_field(payload: dict, *names: str, default=None):
    for name in names:
        if name in payload:
            return payload[name]
    return default


def slugify(value: str) -> str:
    lowered = value.lower().replace(" ", "_").replace("-", "_")
    return "".join(char if char.isalnum() or char == "_" else "_" for char in lowered).strip("_") or "workspace"


def derive_wing(payload: dict) -> str:
    cwd = Path(get_field(payload, "cwd", default=".")).expanduser()
    try:
        resolved = cwd.resolve()
    except OSError:
        resolved = cwd
    base = slugify(resolved.name or "workspace")
    stable_suffix = hashlib.md5(str(resolved).encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{base}_{stable_suffix}"


def derive_cache_root(payload: dict, wing: str) -> Path:
    cwd_value = get_field(payload, "cwd", default="")
    if cwd_value:
        cwd = Path(cwd_value).expanduser()
        if cwd.is_dir():
            return cwd / ".mempalace-cache" / "copilot-hooks" / wing
    return Path(__file__).resolve().parent / EXPORTS_DIRNAME / wing


def read_transcript_records(transcript: str) -> list[dict]:
    records = []
    malformed_lines = 0
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            malformed_lines += 1
    if malformed_lines:
        warn(f"skipped {malformed_lines} malformed transcript JSONL line(s)")
    return records


def compact_words(text: str, limit: int = 12) -> str:
    words = text.replace("\n", " ").split()
    return " ".join(words[:limit]).strip()


def normalize_text_block(text: str) -> str:
    return "\n".join(line.rstrip() for line in str(text).strip().splitlines()).strip()


def derive_transcript_label(payload: dict, records: list[dict], fallback_timestamp: str, source: Path) -> tuple[str, str, str]:
    session_date = fallback_timestamp[:10] if fallback_timestamp else "unknown-date"
    first_prompt = source.stem
    session_key_source = get_field(
        payload,
        "session_id",
        "sessionId",
        "conversationId",
        "chatSessionId",
        default="",
    )

    for record in records:
        if record.get("type") == "session.start":
            session_data = record.get("data", {})
            start_time = get_field(session_data, "startTime") or record.get("timestamp", "")
            if start_time:
                session_date = start_time[:10]
            if not session_key_source:
                session_key_source = get_field(session_data, "sessionId", "session_id", "id", default="")
        if record.get("type") == "user.message":
            content = get_field(record.get("data", {}), "content", default="")
            if content:
                first_prompt = compact_words(content)
                break

    label_slug = slugify(first_prompt)[:64]
    if not session_key_source:
        try:
            source_identity = str(source.resolve())
        except OSError:
            source_identity = str(source)
        session_marker = fallback_timestamp or session_date
        session_key_source = f"{session_marker}:{source_identity}:{label_slug or 'chat'}"
    session_key = hashlib.md5(session_key_source.encode(), usedforsecurity=False).hexdigest()[:16]
    return session_date, label_slug or "chat", session_key


def render_transcript_export(records: list[dict]) -> str:
    lines: list[str] = []
    last_role = None
    rendered_turns = 0
    unexpected_types: set[str] = set()

    for record in records:
        record_type = record.get("type")
        data = record.get("data", {})

        if record_type and record_type not in KNOWN_RECORD_TYPES:
            unexpected_types.add(str(record_type))

        if record_type == "user.message":
            content = normalize_text_block(get_field(data, "content", default=""))
            if not content:
                continue
            if lines and last_role is not None:
                lines.append("")
            lines.extend(f"> {line}" if line else ">" for line in content.splitlines())
            last_role = "user"
            rendered_turns += 1
            continue

        if record_type == "assistant.message":
            content = normalize_text_block(get_field(data, "content", default=""))
            if not content:
                continue
            if lines and last_role is not None:
                lines.append("")
            lines.extend(content.splitlines())
            last_role = "assistant"
            rendered_turns += 1

    if unexpected_types:
        warn(
            "saw unrecognized transcript record type(s): "
            + ", ".join(sorted(unexpected_types)[:8])
        )
    if records and rendered_turns == 0:
        seen_types = sorted({str(record.get("type", "")) for record in records if record.get("type")})
        warn(
            "transcript did not contain renderable user/assistant turns; seen record type(s): "
            + ", ".join(seen_types[:8])
        )

    return "\n".join(lines).strip()


def export_transcript_file(
    payload: dict,
    wing: str,
    session_date: str,
    label_slug: str,
    session_hash: str,
    transcript_text: str,
) -> Path:
    exports_root = derive_cache_root(payload, wing)
    session_dir = exports_root / f"{session_date}_{label_slug}_{session_hash}"
    session_dir.mkdir(parents=True, exist_ok=True)

    export_path = session_dir / "transcript.txt"
    export_path.write_text(transcript_text + "\n", encoding="utf-8")
    try:
        return export_path.resolve()
    except OSError:
        return export_path


def derive_full_transcript_path(export_path: Path) -> Path:
    full_path = export_path.with_name(FULL_TRANSCRIPT_FILENAME)
    try:
        return full_path.resolve()
    except OSError:
        return full_path


def derive_session_title(export_path: Path) -> str:
    session_dir = export_path.parent.name or export_path.stem
    parts = session_dir.split("_")
    if len(parts) >= 3:
        session_hash = parts[-1]
        session_slug = " ".join(parts[1:-1]).strip() or export_path.stem
        return f"{parts[0]} | {session_slug} | {session_hash}"
    return session_dir.replace("_", " ").strip() or export_path.stem


def build_explicit_transcript_document(session_title: str, export_path: Path, transcript_text: str) -> str:
    header_lines = [
        "[Copilot Transcript Full Record]",
        f"Session Title: {session_title}",
        f"Session Folder: {export_path.parent.name}",
        f"Canonical Transcript Source: {export_path.name}",
        "Record Class: explicit fallback | migration data | future-proof data | long-form | safer verbatim | rebuildable",
        "Storage Contract: one singular explicit drawer preserved separately from any MemPalace mine output",
        "",
        "--- BEGIN VERBATIM TRANSCRIPT ---",
    ]
    footer_lines = ["--- END VERBATIM TRANSCRIPT ---"]
    return "\n".join(header_lines + [transcript_text] + footer_lines).strip()


def build_bridge_hook_url() -> str:
    bridge_url = os.environ.get("MEMPALACE_BRIDGE_URL", DEFAULT_BRIDGE_URL).strip() or DEFAULT_BRIDGE_URL
    endpoint = os.environ.get("MEMPALACE_BRIDGE_ENDPOINT", "").strip()
    parsed = parse.urlparse(bridge_url)

    if parsed.scheme and parsed.netloc and parsed.path not in ("", "/") and not endpoint:
        return bridge_url

    if not endpoint:
        endpoint = DEFAULT_BRIDGE_ENDPOINT
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"

    if parsed.scheme and parsed.netloc:
        return parse.urlunparse((parsed.scheme, parsed.netloc, endpoint, "", "", ""))
    return bridge_url.rstrip("/") + endpoint


def get_bridge_timeout_seconds() -> float:
    raw = os.environ.get("MEMPALACE_BRIDGE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_BRIDGE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        warn(f"invalid MEMPALACE_BRIDGE_TIMEOUT_SECONDS={raw!r}; using default {DEFAULT_BRIDGE_TIMEOUT_SECONDS}")
        return DEFAULT_BRIDGE_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_BRIDGE_TIMEOUT_SECONDS


def build_bridge_payload(
    payload: dict,
    wing: str,
    session_date: str,
    label_slug: str,
    session_hash: str,
    export_path: Path,
    full_export_path: Path,
    session_title: str,
    transcript_text: str,
    transcript_document: str,
) -> dict:
    hostname = socket.gethostname()
    return {
        "wing": wing,
        "session_date": session_date,
        "label_slug": label_slug,
        "session_hash": session_hash,
        "session_folder": export_path.parent.name,
        "session_title": session_title,
        "transcript_text": transcript_text,
        "transcript_document": transcript_document,
        "client_export_path": str(export_path),
        "client_full_export_path": str(full_export_path),
        "client_cwd": get_field(payload, "cwd", default=""),
        "client_hostname": hostname,
        "client_timestamp": get_field(payload, "timestamp", default=""),
        "added_by": ADDED_BY,
        "mine_extract_mode": MINE_EXTRACT_MODE,
        "full_transcript_room": FULL_TRANSCRIPT_ROOM,
        "full_transcript_extract_mode": FULL_TRANSCRIPT_EXTRACT_MODE,
        "full_transcript_filename": FULL_TRANSCRIPT_FILENAME,
    }


def submit_transcript_to_bridge(bridge_payload: dict) -> bool:
    bridge_url = build_bridge_hook_url()
    request_body = json.dumps(bridge_payload).encode("utf-8")
    http_request = request.Request(
        bridge_url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=get_bridge_timeout_seconds()) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            status_code = getattr(response, "status", None) or response.getcode()
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        warn(f"bridge request failed with HTTP {exc.code}: {response_body.strip() or exc.reason}")
        return False
    except error.URLError as exc:
        warn(f"bridge unavailable at {bridge_url}: {exc.reason}")
        return False
    except TimeoutError:
        warn(f"bridge request timed out after {get_bridge_timeout_seconds()}s: {bridge_url}")
        return False
    except Exception as exc:
        warn(f"unexpected bridge failure for {bridge_url}: {exc}")
        return False

    if status_code < 200 or status_code >= 300:
        warn(f"bridge returned HTTP {status_code}: {response_body.strip()}")
        return False

    try:
        response_payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        warn(f"bridge returned malformed JSON: {exc.msg}")
        return False

    if not isinstance(response_payload, dict):
        warn("bridge returned a non-object response")
        return False

    if response_payload.get("ok") is not True:
        details = response_payload.get("error") or response_payload.get("warnings") or response_payload
        warn(f"bridge did not confirm transcript ingest: {details}")
        return False

    remote_export_path = response_payload.get("remote_export_path")
    remote_full_export_path = response_payload.get("remote_full_export_path")
    if not isinstance(remote_export_path, str) or not isinstance(remote_full_export_path, str):
        warn("bridge response was missing remote transcript paths")
        return False

    warnings = response_payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        warn("bridge completed with warnings: " + " | ".join(str(item) for item in warnings[:5]))

    return True


def maybe_store_transcript(payload: dict) -> None:
    event_name = get_field(payload, "hook_event_name", "hookEventName", default="")
    if event_name not in MINE_EVENTS:
        return

    transcript_path = get_field(payload, "transcript_path", "transcriptPath")
    if not transcript_path:
        return

    source = Path(transcript_path).expanduser()
    if not source.is_file():
        return

    transcript = source.read_text(encoding="utf-8", errors="replace")
    if not transcript.strip():
        return

    wing = derive_wing(payload)
    timestamp = get_field(payload, "timestamp", default="")
    if source.suffix.lower() == ".txt":
        records = []
    else:
        records = read_transcript_records(transcript)
    session_date, label_slug, session_hash = derive_transcript_label(payload, records, timestamp, source)

    if records:
        transcript_text = render_transcript_export(records)
        if not transcript_text:
            warn(f"no user/assistant turns rendered from transcript {source}")
            return
        export_path = export_transcript_file(
            payload=payload,
            wing=wing,
            session_date=session_date,
            label_slug=label_slug,
            session_hash=session_hash,
            transcript_text=transcript_text,
        )
    elif source.suffix.lower() == ".txt":
        transcript_text = normalize_text_block(transcript)
        if not transcript_text:
            return
        try:
            export_path = source.resolve()
        except OSError:
            export_path = source
    else:
        warn(f"no parseable transcript records found in {source}")
        return

    full_export_path = derive_full_transcript_path(export_path)
    session_title = derive_session_title(export_path)
    transcript_document = build_explicit_transcript_document(session_title, export_path, transcript_text)
    full_export_path.write_text(transcript_document + "\n", encoding="utf-8")

    bridge_payload = build_bridge_payload(
        payload=payload,
        wing=wing,
        session_date=session_date,
        label_slug=label_slug,
        session_hash=session_hash,
        export_path=export_path,
        full_export_path=full_export_path,
        session_title=session_title,
        transcript_text=transcript_text,
        transcript_document=transcript_document,
    )
    submit_transcript_to_bridge(bridge_payload)


def main() -> int:
    raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        print(json.dumps({"continue": True}))
        return 0

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        warn(f"invalid hook payload JSON: {exc.msg}")
        print(json.dumps({"continue": True}))
        return 0

    if isinstance(payload, dict):
        maybe_store_transcript(payload)
    else:
        warn("hook payload was not a JSON object")

    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())