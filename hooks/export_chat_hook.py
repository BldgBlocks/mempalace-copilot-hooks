#!/usr/bin/env python3

import hashlib
import json
import sys
from pathlib import Path

import chromadb
from mempalace.config import MempalaceConfig

TRANSCRIPT_ROOM = "chat-transcript"
ADDED_BY = "GitHub Copilot Hook"


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
    return slugify(cwd.name or "workspace")


def get_collection():
    palace_path = Path(MempalaceConfig().palace_path).expanduser()
    palace_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(palace_path))
    try:
        return client.get_collection("mempalace_drawers")
    except Exception:
        return client.create_collection("mempalace_drawers")


def upsert_drawer(collection, drawer_id: str, document: str, metadata: dict) -> None:
    collection.upsert(ids=[drawer_id], documents=[document], metadatas=[metadata])


def read_transcript_records(transcript: str) -> list[dict]:
    records = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def compact_words(text: str, limit: int = 12) -> str:
    words = text.replace("\n", " ").split()
    return " ".join(words[:limit]).strip()


def derive_transcript_label(records: list[dict], fallback_timestamp: str, source: Path) -> tuple[str, str, str]:
    session_date = fallback_timestamp[:10] if fallback_timestamp else "unknown-date"
    first_prompt = source.stem

    for record in records:
        if record.get("type") == "session.start":
            start_time = get_field(record.get("data", {}), "startTime") or record.get("timestamp", "")
            if start_time:
                session_date = start_time[:10]
        if record.get("type") == "user.message":
            content = get_field(record.get("data", {}), "content", default="")
            if content:
                first_prompt = compact_words(content)
                break

    label_slug = slugify(first_prompt)[:64]
    readable_label = f"chat {session_date} - {first_prompt}".strip()
    return session_date, label_slug or "chat", readable_label


def cleanup_legacy_drawers(collection, session_id: str, canonical_drawer_id: str) -> None:
    try:
        existing = collection.get(where={"session_id": session_id}, include=[])
    except Exception:
        return

    stale_ids = [drawer_id for drawer_id in existing.get("ids", []) if drawer_id != canonical_drawer_id]
    if stale_ids:
        collection.delete(ids=stale_ids)


def maybe_store_transcript(payload: dict) -> None:
    event_name = get_field(payload, "hook_event_name", "hookEventName", default="")
    if event_name not in {"UserPromptSubmit", "PreCompact", "Stop"}:
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
    session_id = get_field(payload, "session_id", "sessionId", default="unknown-session")
    timestamp = get_field(payload, "timestamp", default="")
    records = read_transcript_records(transcript)
    session_date, label_slug, readable_label = derive_transcript_label(records, timestamp, source)
    session_hash = hashlib.md5(session_id.encode(), usedforsecurity=False).hexdigest()[:16]
    drawer_id = f"drawer_{wing}_{TRANSCRIPT_ROOM}_{session_date}_{label_slug}_{session_hash}"
    collection = get_collection()

    cleanup_legacy_drawers(collection, session_id, drawer_id)

    upsert_drawer(
        collection,
        drawer_id,
        transcript,
        {
            "wing": wing,
            "room": TRANSCRIPT_ROOM,
            "source_file": readable_label,
            "transcript_path": str(source),
            "added_by": ADDED_BY,
            "filed_at": timestamp,
            "session_id": session_id,
            "session_date": session_date,
            "session_label": readable_label,
            "hook_event_name": event_name,
            "ingest_mode": "hook-transcript",
        },
    )


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
