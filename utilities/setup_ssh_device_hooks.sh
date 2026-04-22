#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)

bridge_url="http://10.0.0.12:3940"
sync_prompts=0
sync_mcp=0
dry_run=0
user_dir="$HOME/.config/Code/User"
remote_machine_dir="$HOME/.vscode-server/data/Machine"
remote_user_dir="$HOME/.vscode-server/data/User"

usage() {
    cat <<'EOF'
Usage: setup_ssh_device_hooks.sh [options]

Bootstrap Copilot hook support for a Linux Remote-SSH device without installing MemPalace.

Options:
  --bridge-url URL         Bridge base URL. Default: http://10.0.0.12:3940
  --user-dir PATH          VS Code user dir for deployed hook files. Default: ~/.config/Code/User
  --remote-machine-dir PATH
                           Remote-SSH machine settings dir. Default: ~/.vscode-server/data/Machine
  --remote-user-dir PATH   Remote-SSH user data dir. Default: ~/.vscode-server/data/User
  --sync-prompts           Also copy prompts/instructions into the remote user prompts dir
  --sync-mcp               Also write mcp.json into the remote user dir
  --dry-run                Show planned writes without changing files
  -h, --help               Show this help message

This script installs the hook files into the remote host profile, updates the
desktop-style user settings, and also enables hook discovery in the remote
machine settings used by VS Code Remote-SSH.

This script currently targets Linux SSH hosts only. It assumes Bash, python3,
and Linux-style VS Code server paths under ~/.config and ~/.vscode-server.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bridge-url)
            bridge_url="$2"
            shift 2
            ;;
        --user-dir)
            user_dir="$2"
            shift 2
            ;;
        --remote-machine-dir)
            remote_machine_dir="$2"
            shift 2
            ;;
        --remote-user-dir)
            remote_user_dir="$2"
            shift 2
            ;;
        --sync-prompts)
            sync_prompts=1
            shift
            ;;
        --sync-mcp)
            sync_mcp=1
            shift
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

python_cmd=${PYTHON:-python3}
common_args=(--bridge-url "$bridge_url" --user-dir "$user_dir")

if [[ $sync_mcp -eq 0 ]]; then
    common_args+=(--skip-mcp)
fi

if [[ $sync_prompts -eq 1 ]]; then
    common_args+=(--sync-prompts)
fi

if [[ $dry_run -eq 1 ]]; then
    common_args+=(--dry-run)
fi

echo "SYNC user profile hook files"
"$python_cmd" "$repo_root/utilities/sync_live_vscode.py" "${common_args[@]}"

remote_machine_settings="$remote_machine_dir/settings.json"
echo "SYNC remote machine hook discovery"
REMOTE_MACHINE_SETTINGS="$remote_machine_settings" DRY_RUN="$dry_run" "$python_cmd" - <<'PY'
import json
import os
from pathlib import Path

target = Path(os.environ["REMOTE_MACHINE_SETTINGS"]).expanduser()
dry_run = os.environ.get("DRY_RUN") == "1"

if target.exists():
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {target}")
else:
    data = {}

hook_locations = data.setdefault("chat.hookFilesLocations", {})
if not isinstance(hook_locations, dict):
    raise ValueError(f"expected chat.hookFilesLocations object in {target}")
hook_locations["~/.config/Code/User/copilot-hooks"] = True

print(f"WRITE {target}")
if not dry_run:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

if [[ $sync_prompts -eq 1 ]]; then
    prompts_dir="$remote_user_dir/prompts"
    echo "SYNC remote user prompts"
    if [[ $dry_run -eq 1 ]]; then
        echo "WRITE $prompts_dir/mempalace.instructions.md"
        echo "WRITE $prompts_dir/mpingest.prompt.md"
    else
        mkdir -p "$prompts_dir"
        cp "$repo_root/instructions/mempalace.instructions.md" "$prompts_dir/mempalace.instructions.md"
        cp "$repo_root/prompts/mpingest.prompt.md" "$prompts_dir/mpingest.prompt.md"
    fi
fi

if [[ $sync_mcp -eq 1 ]]; then
    echo "SYNC remote user MCP config"
    remote_mcp_args=(--bridge-url "$bridge_url" --user-dir "$remote_user_dir")
    if [[ $sync_prompts -eq 1 ]]; then
        remote_mcp_args+=(--sync-prompts)
    fi
    if [[ $dry_run -eq 1 ]]; then
        remote_mcp_args+=(--dry-run)
    fi
    "$python_cmd" "$repo_root/utilities/sync_live_vscode.py" "${remote_mcp_args[@]}"
fi

echo "DONE"
echo "Reload the Remote-SSH window to make VS Code pick up updated hook settings."