from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_HOOK_PYTHON = "/home/admin/.venvs/mempalace-copilot/bin/python"
DEFAULT_HOOK_SCRIPT = "/home/admin/.config/Code/User/copilot-hooks/export_chat_hook.py"
DEFAULT_CACHE_DIRNAME = ".mempalace-cache/imports"
SKIP_DIRS = {".git", ".venv", ".mempalace-cache", "node_modules", "__pycache__"}
MARKDOWN_SUFFIXES = {".md", ".markdown"}
DIRECT_INGEST_SUFFIXES = {".jsonl", ".txt"}
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES | DIRECT_INGEST_SUFFIXES


@dataclass
class ConversionResult:
    source: Path
    transcript: Path
    blocks: int


@dataclass
class SkipRecord:
    path: Path
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Markdown chat exports to transcript.txt files and ingest "
            "Markdown, transcript.txt, or hook-readable JSONL through the deployed hook."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=["."],
        help="Input files or directories. Defaults to the current directory.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root used as hook cwd. Defaults to the current directory.",
    )
    parser.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIRNAME,
        help="Cache directory for generated transcript.txt files.",
    )
    parser.add_argument(
        "--mode",
        choices=["convert", "ingest", "all"],
        default="all",
        help=(
            "convert: only generate transcript.txt files from Markdown; "
            "ingest: ingest .txt/.jsonl inputs or cached transcript.txt files; "
            "all: convert Markdown, then ingest converted and direct transcript inputs."
        ),
    )
    parser.add_argument(
        "--hook-python",
        default=DEFAULT_HOOK_PYTHON,
        help="Python interpreter used to run the deployed MemPalace hook.",
    )
    parser.add_argument(
        "--hook-script",
        default=DEFAULT_HOOK_SCRIPT,
        help="Path to the deployed MemPalace hook script.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of discovered source files to process. 0 means no limit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print one line per converted, skipped, or ingested file.",
    )
    return parser.parse_args()


def resolve_input_path(item: str, workspace: Path) -> Path:
    path = Path(item).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (workspace / path).resolve()


def should_skip_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def is_supported_source(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def discover_sources(inputs: list[str], workspace: Path, limit: int) -> tuple[list[Path], list[SkipRecord]]:
    seen: set[Path] = set()
    discovered: list[Path] = []
    skipped: list[SkipRecord] = []

    for item in inputs:
        path = resolve_input_path(item, workspace)

        if not path.exists():
            skipped.append(SkipRecord(path=path, reason="path does not exist"))
            continue

        if path.is_file():
            if not is_supported_source(path):
                skipped.append(SkipRecord(path=path, reason="unsupported file type"))
                continue
            if path not in seen:
                seen.add(path)
                discovered.append(path)
            if limit and len(discovered) >= limit:
                return discovered, skipped
            continue

        if not path.is_dir():
            skipped.append(SkipRecord(path=path, reason="not a regular file or directory"))
            continue

        for child in sorted(path.rglob("*")):
            if not child.is_file() or should_skip_path(child):
                continue
            if not is_supported_source(child):
                continue
            resolved = child.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            discovered.append(resolved)
            if limit and len(discovered) >= limit:
                return discovered, skipped

    return discovered, skipped


def is_timestamp_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped.startswith("*") and stripped.endswith("*")


def parse_markdown_export(source: Path) -> list[tuple[str, str]]:
    lines = source.read_text(encoding="utf-8").splitlines()
    blocks: list[tuple[str, str]] = []
    speaker: str | None = None
    buffer: list[str] = []
    seen_title = False

    def flush() -> None:
        if speaker is None:
            return
        text = "\n".join(buffer).strip("\n").strip()
        if text:
            blocks.append((speaker, text))

    for line in lines:
        if not seen_title and line.startswith("# "):
            seen_title = True
            continue
        if line.strip() == "---":
            continue
        if is_timestamp_line(line):
            continue
        if line.startswith("### "):
            flush()
            speaker = line[4:].strip()
            buffer = []
            continue
        if speaker is None:
            continue
        buffer.append(line)

    flush()
    return blocks


def blocks_to_transcript(blocks: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for speaker, text in blocks:
        if speaker.lower() == "you":
            parts.append("\n".join(("> " + line) if line else ">" for line in text.splitlines()))
        else:
            parts.append(text)
    return "\n\n".join(parts).strip() + "\n"


def conversion_folder_name(source: Path) -> str:
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", source.stem).strip("-") or "chat-export"
    return f"{slug}_{digest}"


def convert_sources(sources: list[Path], cache_root: Path, verbose: bool) -> tuple[list[ConversionResult], list[SkipRecord]]:
    converted: list[ConversionResult] = []
    skipped: list[SkipRecord] = []

    for source in sources:
        blocks = parse_markdown_export(source)
        if not blocks:
            skipped.append(SkipRecord(path=source, reason="Markdown export did not contain any speaker blocks"))
            if verbose:
                print(f"SKIP {source} reason=empty-markdown-export")
            continue

        output_dir = cache_root / conversion_folder_name(source)
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = output_dir / "transcript.txt"
        transcript_path.write_text(blocks_to_transcript(blocks), encoding="utf-8")
        converted.append(ConversionResult(source=source, transcript=transcript_path, blocks=len(blocks)))
        if verbose:
            print(f"CONVERT OK {source} -> {transcript_path} blocks={len(blocks)}")

    return converted, skipped


def find_existing_transcripts(cache_root: Path) -> list[Path]:
    if not cache_root.exists():
        return []
    return sorted(path.resolve() for path in cache_root.rglob("transcript.txt") if path.is_file())


def preflight_hook(hook_python: str, hook_script: str) -> tuple[bool, str]:
    result = subprocess.run(
        [hook_python, "-c", "import mempalace; print('PREFLIGHT_OK')"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    hook_exists = Path(hook_script).exists()
    if not hook_exists:
        return False, f"hook script not found: {hook_script}"
    return True, result.stdout.strip()


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def ingest_transcripts(
    transcripts: list[Path],
    workspace: Path,
    hook_python: str,
    hook_script: str,
    verbose: bool,
) -> tuple[int, int, list[str]]:
    succeeded = 0
    failed = 0
    warnings: list[str] = []

    for transcript in transcripts:
        session_id = hashlib.sha256(str(transcript).encode("utf-8")).hexdigest()
        payload = {
            "hook_event_name": "Stop",
            "transcript_path": str(transcript),
            "cwd": str(workspace),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sessionId": session_id,
        }
        result = subprocess.run(
            [hook_python, hook_script],
            input=json.dumps(payload) + "\n",
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            succeeded += 1
        else:
            failed += 1
        stderr = result.stderr.strip()
        if stderr:
            warnings.append(f"{transcript}: {stderr.replace(chr(10), ' | ')}")
        if verbose:
            status = "OK" if result.returncode == 0 else "FAIL"
            print(f"INGEST {status} {transcript}")

    return succeeded, failed, warnings


def split_sources_by_type(sources: list[Path]) -> tuple[list[Path], list[Path]]:
    markdown_sources: list[Path] = []
    direct_sources: list[Path] = []

    for source in sources:
        suffix = source.suffix.lower()
        if suffix in MARKDOWN_SUFFIXES:
            markdown_sources.append(source)
        elif suffix in DIRECT_INGEST_SUFFIXES:
            direct_sources.append(source)

    return markdown_sources, direct_sources


def print_summary(
    discovered: list[Path],
    converted: list[ConversionResult],
    direct_sources: list[Path],
    skipped: list[SkipRecord],
    transcripts: list[Path],
    succeeded: int,
    failed: int,
    warnings: list[str],
) -> None:
    print(
        "SUMMARY "
        f"discovered={len(discovered)} "
        f"converted={len(converted)} "
        f"passthrough={len(direct_sources)} "
        f"transcripts={len(transcripts)} "
        f"skipped={len(skipped)} "
        f"ingested_ok={succeeded} "
        f"ingested_failed={failed} "
        f"warnings={len(warnings)}"
    )
    for record in skipped[:10]:
        print(f"SKIP {record.path} reason={record.reason}")
    if len(skipped) > 10:
        print(f"SKIP_MORE {len(skipped) - 10}")
    for warning in warnings[:10]:
        print(f"WARN {warning}")
    if len(warnings) > 10:
        print(f"WARN_MORE {len(warnings) - 10}")


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.exists() or not workspace.is_dir():
        print(f"WORKSPACE FAIL workspace directory not found: {workspace}")
        return 1

    cache_dir = Path(args.cache_dir)
    cache_root = (workspace / cache_dir).resolve() if not cache_dir.is_absolute() else cache_dir.resolve()

    discovered, skipped = discover_sources(args.inputs, workspace, args.limit)
    markdown_sources, direct_sources = split_sources_by_type(discovered)
    converted: list[ConversionResult] = []

    if args.mode in {"convert", "all"} and markdown_sources:
        converted, conversion_skips = convert_sources(markdown_sources, cache_root, args.verbose)
        skipped.extend(conversion_skips)
    elif args.mode == "ingest":
        for source in markdown_sources:
            skipped.append(SkipRecord(path=source, reason="Markdown input requires --mode convert or --mode all"))
            if args.verbose:
                print(f"SKIP {source} reason=markdown-requires-convert")

    transcripts: list[Path] = []
    if args.mode == "convert":
        direct_sources = []
    elif args.mode == "ingest":
        if direct_sources:
            transcripts = unique_paths(direct_sources)
        else:
            transcripts = find_existing_transcripts(cache_root)
    else:
        transcripts = unique_paths([item.transcript for item in converted] + direct_sources)

    if args.mode in {"ingest", "all"}:
        ok, preflight_output = preflight_hook(args.hook_python, args.hook_script)
        if not ok:
            print(f"PREFLIGHT FAIL {preflight_output}")
            return 1
        if args.verbose:
            print(f"PREFLIGHT OK {preflight_output}")
        succeeded, failed, warnings = ingest_transcripts(
            transcripts=transcripts,
            workspace=workspace,
            hook_python=args.hook_python,
            hook_script=args.hook_script,
            verbose=args.verbose,
        )
    else:
        succeeded = 0
        failed = 0
        warnings = []

    print_summary(
        discovered=discovered,
        converted=converted,
        direct_sources=direct_sources,
        skipped=skipped,
        transcripts=transcripts,
        succeeded=succeeded,
        failed=failed,
        warnings=warnings,
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())