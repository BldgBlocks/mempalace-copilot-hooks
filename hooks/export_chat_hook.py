#!/usr/bin/env python3

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mempalace.config import MempalaceConfig
from mempalace.palace import (
    NORMALIZE_VERSION,
    build_closet_lines,
    get_closets_collection as palace_get_closets_collection,
    get_collection as palace_get_collection,
    purge_file_closets,
    upsert_closet_lines,
)

ADDED_BY = "GitHub Copilot Hook"
MINE_EVENTS = {"UserPromptSubmit", "PreCompact", "Stop"}
EXPORTS_DIRNAME = "exports"
FULL_TRANSCRIPT_ROOM = "chat_transcript_full"
FULL_TRANSCRIPT_EXTRACT_MODE = "verbatim_full"
MINE_EXTRACT_MODE = "exchange"
FULL_TRANSCRIPT_FILENAME = "transcript.full.raw"
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
    # - The raw transcript drawer and closets are keyed by source_file, so the
    #   cache path must be stable across repeated hook runs for the same session.
    # - A workspace-local .mempalace-cache keeps data out of unrelated user
    #   config folders while still giving the palace a durable replacement key.
    cwd_value = get_field(payload, "cwd", default="")
    if cwd_value:
        cwd = Path(cwd_value).expanduser()
        if cwd.is_dir():
            return cwd / ".mempalace-cache" / "copilot-hooks" / wing
    return Path(__file__).resolve().parent / EXPORTS_DIRNAME / wing


def get_drawers_collection(palace_path: Path):
    palace_path.mkdir(parents=True, exist_ok=True)
    try:
        return palace_get_collection(str(palace_path), collection_name="mempalace_drawers", create=True)
    except Exception as exc:
        warn(f"could not open mempalace_drawers collection at {palace_path}: {exc}")
        return None


def get_closets_collection(palace_path: Path):
    palace_path.mkdir(parents=True, exist_ok=True)
    try:
        return palace_get_closets_collection(str(palace_path), create=True)
    except Exception as exc:
        warn(f"could not open mempalace_closets collection at {palace_path}: {exc}")
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


def transcript_drawer_id(wing: str, source_file: str) -> str:
    source_hash = hashlib.sha256(source_file.encode(), usedforsecurity=False).hexdigest()[:24]
    return f"drawer_{wing}_{FULL_TRANSCRIPT_ROOM}_{source_hash}"


def transcript_closet_id_base(wing: str, source_file: str) -> str:
    source_hash = hashlib.sha256(source_file.encode(), usedforsecurity=False).hexdigest()[:24]
    return f"closet_{wing}_{FULL_TRANSCRIPT_ROOM}_{source_hash}"


def cleanup_transcript_artifacts(drawers_col, closets_col, source_file: str) -> None:
    if drawers_col is not None:
        try:
            drawers_col.delete(where={"source_file": source_file})
        except Exception as exc:
            warn(f"could not delete existing drawers for {source_file}: {exc}")
    if closets_col is not None:
        try:
            purge_file_closets(closets_col, source_file)
        except Exception as exc:
            warn(f"could not delete existing closets for {source_file}: {exc}")


def file_transcript_drawer(drawers_col, wing: str, source_file: str, session_title: str, transcript_document: str) -> str | None:
    if drawers_col is None:
        return None

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
                    "chunk_index": 0,
                    "added_by": ADDED_BY,
                    "filed_at": datetime.now().isoformat(),
                    "title": session_title,
                    "record_class": "explicit_fallback_migration_futureproof_longform_rebuildable",
                    "ingest_mode": "hook_transcript_full",
                    "extract_mode": FULL_TRANSCRIPT_EXTRACT_MODE,
                    "normalize_version": NORMALIZE_VERSION,
                }
            ],
        )
        return drawer_id
    except Exception as exc:
        warn(f"could not file transcript drawer for {source_file}: {exc}")
        return None


def file_transcript_closets(closets_col, wing: str, source_file: str, drawer_id: str, transcript_text: str) -> None:
    if closets_col is None:
        return

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
                "added_by": ADDED_BY,
                "filed_at": datetime.now().isoformat(),
                "record_class": "explicit_fallback_migration_futureproof_longform_rebuildable",
                "ingest_mode": "hook_transcript_full",
                "extract_mode": FULL_TRANSCRIPT_EXTRACT_MODE,
                "normalize_version": NORMALIZE_VERSION,
            },
        )
    except Exception as exc:
        warn(f"could not file transcript closets for {source_file}: {exc}")


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

    palace_path = Path(MempalaceConfig().palace_path).expanduser()
    drawers_col = get_drawers_collection(palace_path)
    closets_col = get_closets_collection(palace_path)
    full_export_path = derive_full_transcript_path(export_path)
    session_title = derive_session_title(export_path)
    transcript_document = build_explicit_transcript_document(session_title, export_path, transcript_text)
    full_export_path.write_text(transcript_document + "\n", encoding="utf-8")
    cleanup_transcript_artifacts(drawers_col, closets_col, str(full_export_path))
    drawer_id = file_transcript_drawer(drawers_col, wing, str(full_export_path), session_title, transcript_document)
    if drawer_id:
        file_transcript_closets(closets_col, wing, str(full_export_path), drawer_id, transcript_text)
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
