#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)

target=""
bridge_url="http://10.0.0.12:3940"
remote_tmp_base='${HOME}/.cache/mempalace-copilot-hooks-bootstrap'
ssh_bin=${SSH_BIN:-ssh}
tar_bin=${TAR_BIN:-tar}
sync_prompts=0
sync_mcp=0
dry_run=0
keep_remote_files=0
ssh_args=()

usage() {
    cat <<'EOF'
Usage: deploy_ssh_device_hooks.sh --target user@host [options]

Push the Linux Remote-SSH hook bootstrap to another device and run it there in one step.

Options:
  --target user@host       SSH target. Required.
  --bridge-url URL         Bridge base URL. Default: http://10.0.0.12:3940
  --remote-tmp-base PATH   Remote temp base dir. Default: ~/.cache/mempalace-copilot-hooks-bootstrap
  --sync-prompts           Also copy prompts/instructions on the remote host
  --sync-mcp               Also write remote mcp.json on the remote host
  --keep-remote-files      Do not delete the remote temp directory after completion
  --dry-run                Show the remote actions without changing files
  --ssh-arg ARG            Extra argument to pass to ssh. Repeat as needed.
  -h, --help               Show this help message

Example:
  bash utilities/deploy_ssh_device_hooks.sh --target pi@raspberrypi --bridge-url http://10.0.0.12:3940

This wrapper currently targets Linux SSH hosts only. It assumes the remote host
has Bash, tar, python3, and Linux-style VS Code server paths under ~/.config
and ~/.vscode-server.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            target="$2"
            shift 2
            ;;
        --bridge-url)
            bridge_url="$2"
            shift 2
            ;;
        --remote-tmp-base)
            remote_tmp_base="$2"
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
        --keep-remote-files)
            keep_remote_files=1
            shift
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --ssh-arg)
            ssh_args+=("$2")
            shift 2
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

if [[ -z "$target" ]]; then
    echo "Missing required --target user@host" >&2
    usage >&2
    exit 2
fi

timestamp=$(date +%Y%m%d%H%M%S)
remote_tmp_dir="$remote_tmp_base/$timestamp"

bootstrap_args=(--bridge-url "$bridge_url")
if [[ $sync_prompts -eq 1 ]]; then
    bootstrap_args+=(--sync-prompts)
fi
if [[ $sync_mcp -eq 1 ]]; then
    bootstrap_args+=(--sync-mcp)
fi
if [[ $dry_run -eq 1 ]]; then
    bootstrap_args+=(--dry-run)
fi

remote_command=$(cat <<EOF
set -euo pipefail
tmp_dir="$remote_tmp_dir"
mkdir -p "\$(dirname -- "$remote_tmp_dir")"
rm -rf "$remote_tmp_dir"
mkdir -p "$remote_tmp_dir"
tar -xzf - -C "$remote_tmp_dir"
bash "$remote_tmp_dir/utilities/setup_ssh_device_hooks.sh" ${bootstrap_args[*]@Q}
EOF
)

if [[ $keep_remote_files -eq 0 ]]; then
    remote_command+=$'\n'
    remote_command+="rm -rf \"$remote_tmp_dir\""
else
    remote_command+=$'\n'
    remote_command+="echo KEEP_REMOTE_FILES $remote_tmp_dir"
fi

echo "PUSH bootstrap payload to $target"
if [[ $dry_run -eq 1 ]]; then
    echo "REMOTE_TMP_DIR $remote_tmp_dir"
    echo "REMOTE_COMMAND_START"
    printf '%s\n' "$remote_command"
    echo "REMOTE_COMMAND_END"
    exit 0
fi

payload_paths=(
    utilities/setup_ssh_device_hooks.sh
    utilities/sync_live_vscode.py
    hooks/export_chat_hook.py
    hooks/export-events.json
    examples/mcp.json
    instructions/mempalace.instructions.md
    prompts/mpingest.prompt.md
)

(
    cd "$repo_root"
    "$tar_bin" -czf - "${payload_paths[@]}"
) | "$ssh_bin" "${ssh_args[@]}" "$target" "$remote_command"

echo "DONE"
echo "Reload the Remote-SSH window after deployment."
