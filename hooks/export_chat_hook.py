#!/usr/bin/env python3

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import chromadb
from mempalace.config import MempalaceConfig

ADDED_BY = "GitHub Copilot Hook"
MINE_EVENTS = {"UserPromptSubmit", "PreCompact", "Stop"}
EXPORTS_DIRNAME = "exports"
MINE_EXTRACT_MODE = "general"
KNOWN_RECORD_TYPES = {"session.start", "user.message", "assistant.message"}


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
    # Keep hook exports on a stable filesystem path.
    #
    # Why not raw in-memory / tmpfs by default:
    # - This hook is a one-shot process; there is no shared in-memory state
    #   across invocations.
    # - MemPalace convo mining deduplicates by source_file, so the cache path
    #   must be stable across repeated hook runs for the same session.
    # - A workspace-local .mempalace-cache keeps data out of unrelated user
    #   config folders while still giving the miner a durable path to replace.
    cwd_value = get_field(payload, "cwd", default="")
    if cwd_value:
        cwd = Path(cwd_value).expanduser()
        if cwd.is_dir():
            return cwd / ".mempalace-cache" / "copilot-hooks" / wing
    return Path(__file__).resolve().parent / EXPORTS_DIRNAME / wing


def get_collection():
    palace_path = Path(MempalaceConfig().palace_path).expanduser()
    palace_path.mkdir(parents=True, exist_ok=True)
    try:
        client = chromadb.PersistentClient(path=str(palace_path))
        return client.get_collection("mempalace_drawers")
    except Exception as exc:
        warn(f"could not open mempalace_drawers collection at {palace_path}: {exc}")
        try:
            client = chromadb.PersistentClient(path=str(palace_path))
            return client.create_collection("mempalace_drawers")
        except Exception as create_exc:
            warn(f"could not create mempalace_drawers collection at {palace_path}: {create_exc}")
            return None


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
    # Normalize the VS Code transcript into MemPalace's plain conversation
    # format before mining.
    #
    # Why render to transcript.txt instead of mining the JSONL directly:
    # - The hook payload transcript uses VS Code event records like
    #   session.start / user.message / assistant.message.
    # - Upstream mempalace normalize() reliably auto-parses only the chat JSON
    #   schemas it knows about. This VS Code hook shape is not one of those
    #   stable, supported schemas, so mining the raw JSONL would treat it as
    #   opaque text and index JSON syntax instead of the conversation.
    # - Rendering to >-marked plain text gives convo mining a stable format
    #   even if VS Code changes JSON field names later.
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


def cleanup_mined_drawers(collection, source_file: str) -> None:
    if collection is None:
        return
    try:
        existing = collection.get(where={"source_file": source_file}, include=[])
    except Exception as exc:
        warn(f"could not query existing drawers for {source_file}: {exc}")
        return

    existing_ids = [drawer_id for drawer_id in existing.get("ids", []) if drawer_id]
    if existing_ids:
        try:
            collection.delete(ids=existing_ids)
        except Exception as exc:
            warn(f"could not delete existing drawers for {source_file}: {exc}")


def run_mine_command(export_path: Path, wing: str, palace_path: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "mempalace.cli",
        "--palace",
        str(palace_path),
        "mine",
        str(export_path.parent),
        "--mode",
        "convos",
        "--wing",
        wing,
        "--extract",
        MINE_EXTRACT_MODE,
        "--agent",
        ADDED_BY,
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=40,
        )
        if result.stderr.strip():
            warn(result.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        warn(f"mempalace mine timed out for {export_path.parent}: {exc}")
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        warn(f"mempalace mine failed for {export_path.parent}: {details}")
    except Exception as exc:
        warn(f"unexpected mine failure for {export_path.parent}: {exc}")


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
    records = read_transcript_records(transcript)
    session_date, label_slug, session_hash = derive_transcript_label(payload, records, timestamp, source)

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
    # Cleanup happens before mine so a repeated hook run for the same session
    # replaces the previous drawers instead of stacking duplicates.
    palace_path = Path(MempalaceConfig().palace_path).expanduser()
    collection = get_collection()
    cleanup_mined_drawers(collection, str(export_path))
    run_mine_command(export_path, wing, palace_path)


def main() -> int:
    raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        print(json.dumps({"continue": True}))
        return 0

    payload = json.loads(raw_payload)
    maybe_store_transcript(payload)

    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
