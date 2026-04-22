from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_BRIDGE_URL = "http://10.0.0.12:3940"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sync the tracked MemPalace hook files from this repo into the active "
            "VS Code user profile."
        ),
    )
    parser.add_argument(
        "--user-dir",
        default=str(Path.home() / ".config" / "Code" / "User"),
        help="VS Code user directory to update. Defaults to ~/.config/Code/User.",
    )
    parser.add_argument(
        "--bridge-url",
        default=DEFAULT_BRIDGE_URL,
        help="Bridge base URL used by the hook command. Defaults to %(default)s.",
    )
    parser.add_argument(
        "--mcp-url",
        default="",
        help="Optional explicit MCP URL. Defaults to <bridge-url>/mcp.",
    )
    parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="Do not update the live mcp.json file.",
    )
    parser.add_argument(
        "--sync-prompts",
        action="store_true",
        help="Also sync prompts/instructions into the live user prompts directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned writes without changing files.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_bridge_url(url: str) -> str:
    return (url or DEFAULT_BRIDGE_URL).strip().rstrip("/") or DEFAULT_BRIDGE_URL


def derive_mcp_url(bridge_url: str, explicit_mcp_url: str) -> str:
    if explicit_mcp_url.strip():
        return explicit_mcp_url.strip()
    return f"{bridge_url}/mcp"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str, dry_run: bool) -> None:
    print(f"WRITE {path}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def sync_hook_files(root: Path, user_dir: Path, bridge_url: str, dry_run: bool) -> None:
    hook_dir = user_dir / "copilot-hooks"
    hook_script_src = root / "hooks" / "export_chat_hook.py"
    hook_events_src = root / "hooks" / "export-events.json"

    hook_script_content = hook_script_src.read_text(encoding="utf-8")
    write_text(hook_dir / "export_chat_hook.py", hook_script_content, dry_run)

    hook_events = load_json(hook_events_src)
    for event_entries in hook_events.get("hooks", {}).values():
        for entry in event_entries:
            if entry.get("type") != "command":
                continue
            entry["command"] = (
                f"MEMPALACE_BRIDGE_URL={bridge_url} python3 "
                "$HOME/.config/Code/User/copilot-hooks/export_chat_hook.py"
            )
    write_text(hook_dir / "export-events.json", json.dumps(hook_events, indent=2) + "\n", dry_run)


def sync_mcp_file(root: Path, user_dir: Path, mcp_url: str, dry_run: bool) -> None:
    target = user_dir / "mcp.json"
    if target.exists():
        data = load_json(target)
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object in {target}")
    else:
        data = load_json(root / "examples" / "mcp.json")

    servers = data.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"expected servers object in {target}")
    servers["mempalace"] = {"type": "http", "url": mcp_url}
    write_text(target, json.dumps(data, indent=2) + "\n", dry_run)


def sync_settings_file(user_dir: Path, dry_run: bool) -> None:
    target = user_dir / "settings.json"
    if target.exists():
        data = load_json(target)
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object in {target}")
    else:
        data = {}

    hook_locations = data.setdefault("chat.hookFilesLocations", {})
    if not isinstance(hook_locations, dict):
        raise ValueError(f"expected chat.hookFilesLocations object in {target}")
    hook_locations["~/.config/Code/User/copilot-hooks"] = True
    write_text(target, json.dumps(data, indent=2) + "\n", dry_run)


def sync_prompt_files(root: Path, user_dir: Path, dry_run: bool) -> None:
    prompts_dir = user_dir / "prompts"
    prompt_files = [
        (root / "instructions" / "mempalace.instructions.md", prompts_dir / "mempalace.instructions.md"),
        (root / "prompts" / "mpingest.prompt.md", prompts_dir / "mpingest.prompt.md"),
    ]
    for source, target in prompt_files:
        write_text(target, source.read_text(encoding="utf-8"), dry_run)


def main() -> int:
    args = parse_args()
    root = repo_root()
    user_dir = Path(args.user_dir).expanduser().resolve()
    bridge_url = normalize_bridge_url(args.bridge_url)
    mcp_url = derive_mcp_url(bridge_url, args.mcp_url)

    sync_hook_files(root, user_dir, bridge_url, args.dry_run)
    sync_settings_file(user_dir, args.dry_run)
    if not args.skip_mcp:
        sync_mcp_file(root, user_dir, mcp_url, args.dry_run)
    if args.sync_prompts:
        sync_prompt_files(root, user_dir, args.dry_run)

    if args.dry_run:
        print("DRY RUN complete")
    else:
        print("SYNC complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
