# Install MemPalace

This repo packages the VS Code integration layer: hook config, hook script, bridge script, instruction file, and config examples.
The client machine running VS Code hooks does not need the MemPalace package installed locally when you use the remote bridge flow.

## Deployment model

Current tested setup:

- bridge host: `10.0.0.12`
- bridge HTTP base URL: `http://10.0.0.12:3940`
- MCP endpoint: `http://10.0.0.12:3940/mcp`
- hook ingest endpoint: `http://10.0.0.12:3940/copilot-hook`

Practical guidance:

- Install the official upstream package from PyPI on the bridge host only.
- Run the HTTP bridge on the same host as the MemPalace DB.
- Point VS Code MCP and hook clients at the bridge URL.

## Bridge host install

1. Create a dedicated virtual environment on the bridge host.
2. Install the official package into it.
3. Run the bridge service from that environment.

Example:

```bash
python3 -m venv "$HOME/.venvs/mempalace-copilot"
source "$HOME/.venvs/mempalace-copilot/bin/activate"
pip install --upgrade pip
pip install --upgrade mempalace
```

## Bridge host verification

```bash
"$HOME/.venvs/mempalace-copilot/bin/mempalace" status
```

If you use a custom palace path, put the global flag before the subcommand:

```bash
"$HOME/.venvs/mempalace-copilot/bin/mempalace" --palace /path/to/palace status
```

To print the exact MCP setup command for your environment:

```bash
"$HOME/.venvs/mempalace-copilot/bin/mempalace" mcp
"$HOME/.venvs/mempalace-copilot/bin/mempalace" --palace /path/to/palace mcp
```

## VS Code client wiring

1. Prefer `python utilities/sync_live_vscode.py --bridge-url http://10.0.0.12:3940` from the repo root to refresh the live VS Code files in one step. If you do it manually instead, put the hook files in `$HOME/.config/Code/User/copilot-hooks/`.
2. Add the hook location snippet from `examples/settings.json` to your VS Code user settings.
3. Add the HTTP MCP server snippet from `examples/mcp.json` to your VS Code user `mcp.json`.
4. Copy `instructions/mempalace.instructions.md` into the user prompts location for the profile you are using.
5. Copy `prompts/mpingest.prompt.md` into the same user prompts location if you want the `/mpingest` slash command available.
6. Reload VS Code.

Keep `utilities/` in the cloned repo. It is not part of the VS Code user prompts path or the deployed hook folder. Those scripts are meant to be run from wherever you cloned this repository, for example `~/src/mempalace-copilot-hooks/utilities/mpimport.py`.

The sync helper updates the live hook files under `$HOME/.config/Code/User/copilot-hooks/`, enables hook discovery in `$HOME/.config/Code/User/settings.json`, and rewrites the `mempalace` entry in `$HOME/.config/Code/User/mcp.json` to the selected HTTP bridge URL while leaving other MCP server entries intact.

For example, a desktop profile may use `~/.config/Code/User/prompts/` while a serve-web profile may use `~/.vscode-server/data/User/prompts/`.
Keeping the files in this repo under `instructions/` and `prompts/` preserves the source, but those workspace paths alone do not guarantee automatic prompt or instruction injection.

For Remote-SSH specifically: MCP can point at the LAN bridge from the desktop client, but hook execution may still happen in the remote workspace host. If that is your setup, install the hook files and `chat.hookFilesLocations` setting in the remote host's VS Code user profile as well. The remote host still does not need a local `mempalace` package; it only needs `python3`, the deployed hook files, and network reachability to the bridge.

For a one-step Remote-SSH bootstrap on a Linux SSH device, run:

```bash
bash utilities/setup_ssh_device_hooks.sh --bridge-url http://10.0.0.12:3940
```

That writes the hook files into `~/.config/Code/User/copilot-hooks/`, enables hook discovery in both `~/.config/Code/User/settings.json` and `~/.vscode-server/data/Machine/settings.json`, and leaves the remote host free of any local MemPalace package dependency. This bootstrap currently targets Linux SSH hosts only.

If you want to push that setup from this machine to another Linux SSH device without first cloning the repo there, run:

```bash
bash utilities/deploy_ssh_device_hooks.sh --target user@host --bridge-url http://10.0.0.12:3940
```

That sends the minimal bootstrap payload over `ssh`, runs the remote setup script on the target, and removes the temporary remote files when finished. It currently assumes a Linux target with Bash, tar, python3, and the standard `~/.config/Code/User` plus `~/.vscode-server/data` layout.

For manual hook replay, or for prompt-driven imported transcript ingest such as `/mpingest`, use the deployed hook with the bridge URL explicitly:

```bash
MEMPALACE_BRIDGE_URL=http://10.0.0.12:3940 python3 "$HOME/.config/Code/User/copilot-hooks/export_chat_hook.py"
```

The client machine only needs `python3` plus network access to the bridge. It does not need a local `mempalace` binary or Python package anymore.

If you are importing Markdown chat exports in the common heading-based format, use the repo helper so conversion and sequential hook replay stay consistent with the prompt and hook contract:

```bash
python utilities/mpimport.py --workspace /path/to/workspace /path/to/export-or-folder
```

That helper writes converted Markdown transcripts into `.mempalace-cache/imports/` and then feeds each resulting `transcript.txt` into the deployed hook one file at a time. It also accepts already-normalized `.txt` and hook-readable `.jsonl` transcript files.

If your bridge host or port changes, override the default with `MEMPALACE_BRIDGE_URL=http://host:port` in your hook command or shell environment.

## Notes

- The hook script exports a normalized `.txt` transcript into the active workspace under `.mempalace-cache/copilot-hooks/<wing>/...` when possible, and that per-workspace cache is the intended persistent location.
- In the same session folder it also writes `transcript.full.raw`, which is the exact record sent to the bridge for one explicit titled `chat_transcript_full` drawer plus deterministic closets. This explicit record is fallback data, migration data, future-proof data, long-form data, safer verbatim data, and rebuildable data.
- After receiving the explicit long-form record, the bridge host runs `mempalace mine --mode convos --extract exchange` against its own stable session folder so upstream MemPalace still ingests `transcript.txt` using its normal conversation logic.
- `mempalace mine --mode convos --extract general` is meant for auto-classifying conversation exports into decisions, milestones, problems, and similar memory types. It is not the right default for hook-captured Copilot transcripts when the primary requirement is raw verbatim recall.
- `mempalace mine --mode convos --extract exchange` preserves more verbatim than `general`, but it can still split a single transcript into many drawers. That is why the hook also files a separate explicit long-form drawer keyed to `transcript.full.raw`.
- The `.txt` normalization is deliberate: the VS Code hook transcript arrives as JSONL event records, and upstream MemPalace does not reliably auto-parse this exact schema as a conversation transcript. Converting it to plain `>`-marked turns gives convo mining a stable, schema-independent input.
- A stable cache path is safer than tmpfs or a pure in-memory handoff because MemPalace refreshes prior drawers by `source_file`. If the path changes on every hook run, deduplication becomes replacement-resistant and old drawers accumulate.
- The hook emits warnings to stderr when bridge submission fails or the bridge returns malformed data, so export-only failures are visible during debugging without blocking the chat flow.
- Wing names include a short hash of the resolved workspace path to avoid collisions when different projects share the same folder basename.
- The export path is stable per session so the hook can clear previously filed explicit fallback drawers for `transcript.full.raw` and re-mine `transcript.txt` predictably.
- The bridge writes its own stable cache under `~/.mempalace-cache/copilot-hooks-bridge/` by default so MemPalace still gets a consistent `source_file` identity on the host where the DB lives.
- If an upstream ChromaDB change ever makes an existing palace unreadable, use `mempalace --palace /path/to/palace migrate` after making sure the palace path is mounted and reachable.
- Optional serve-web-specific preservation files live under `serve-web/`. They are examples, not the default desktop path.
