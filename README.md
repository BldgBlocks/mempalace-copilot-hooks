# mempalace-copilot-hooks

Small standalone repo for the VS Code side of the current MemPalace setup.

This repo is not the MemPalace engine. It is the integration layer around it for Github Copilot use:

- Copilot hook config
- transcript export hook script
- Copilot instruction file
- minimal VS Code config snippets
- validated optional serve-web deployment bundle
- install notes for the MemPalace package currently in use

## What this solves

The fragile part was asking the model itself to be the primary verbatim recorder.
This setup moves raw chat capture into deterministic VS Code hooks instead.
The hook reads the Copilot transcript file, writes a plain-text export, files one explicit long-form verbatim transcript drawer for the session, and then lets MemPalace run its normal conversation mining pass separately.

## Included files

- `hooks/export-events.json`: active hook mapping for `UserPromptSubmit`, `PreCompact`, and `Stop`
- `hooks/export_chat_hook.py`: transcript export script plus MemPalace mining step
- `instructions/mempalace.instructions.md`: simplified instruction file that assumes hooks own raw capture
- `examples/settings.json`: minimal VS Code user settings snippet for hook discovery
- `examples/mcp.json`: minimal VS Code MCP server snippet
- `prompts/mpingest.prompt.md`: reusable slash-command prompt for ingesting imported chat exports through the deployed hook path
- `serve-web/`: sanitized bridge, shim, service, and config examples for a validated serve-web deployment path
- `docs/install-mempalace.md`: package install guidance for the official upstream package

## Current tested package

The local machine this was extracted from currently uses:

- package: `mempalace==3.3.0`
- repo: https://github.com/MemPalace/mempalace
- docs: https://mempalaceofficial.com/

The current hook script and MCP wiring were validated against the official upstream package.

## Quick install outline

```bash
python3 -m venv "$HOME/.venvs/mempalace-copilot"
source "$HOME/.venvs/mempalace-copilot/bin/activate"
pip install --upgrade pip
pip install --upgrade mempalace
```

Then:

1. Copy `hooks/export-events.json` and `hooks/export_chat_hook.py` into `$HOME/.config/Code/User/copilot-hooks/`.
2. The deployed hook writes a normalized `transcript.txt` into the active workspace under `.mempalace-cache/copilot-hooks/<wing>/...` when the workspace path is available.
3. It also writes a separate `transcript.full.raw` record in the same session folder and files that as one explicit titled `chat_transcript_full` drawer plus deterministic closets.
4. It then runs `mempalace mine --mode convos --extract exchange` against the session folder so upstream MemPalace still produces its normal conversation-mined records from `transcript.txt`.
5. That workspace-local cache is the intended steady-state location and is kept in place so later hook runs can rebuild the same explicit fallback record and re-mine the same `source_file` path cleanly. If no workspace path is available, the hook falls back to `$HOME/.config/Code/User/copilot-hooks/exports/<wing>/...`.
6. Merge `examples/settings.json` into VS Code user settings.
7. Merge `examples/mcp.json` into VS Code user `mcp.json`.
8. Copy `instructions/mempalace.instructions.md` into an actual Copilot prompts location if you want it auto-loaded for a given profile.
9. Copy `prompts/mpingest.prompt.md` into the active user prompts directory if you want the `/mpingest` slash command available in chat.
10. Reload VS Code.

The repo copy under `instructions/` is source material for version control. It is not automatically injected into every Copilot session just because the file exists in the workspace.

The repo copy under `prompts/` is also source material for version control. Prompt files become active slash commands only after they are copied into the relevant VS Code user prompts directory.

The `serve-web/` folder documents a working browser-served VS Code path that was validated against the MemPalace HTTP bridge. It is optional, but it is no longer just speculative preservation material.

If you use a custom palace path, generate the exact MCP command with `mempalace --palace /path/to/palace mcp` and mirror that in your MCP config.

If your VS Code MCP config does not expand `$HOME` in the `command` field, replace it with your full local path.

Full details are in `docs/install-mempalace.md`.

## Behavioral notes

- The hook does not keep its own log files.
- It reads the transcript file path provided by VS Code and writes a normalized `.txt` transcript export into `.mempalace-cache` in the active workspace when possible.
- That cache is intentionally per-workspace, not global. Different workspaces get different cache roots and different wings.
- The hook itself does not delete the workspace cache after mining; keeping it is part of the dedupe/replacement strategy.
- If the workspace path is unavailable, exports fall back to `$HOME/.config/Code/User/copilot-hooks/exports/<wing>/...`.
- The normalization step is intentional: the current VS Code hook transcript JSONL is not a stable MemPalace-native chat schema, so mining raw JSONL would not reliably parse user and assistant turns.
- The hook prints warnings to stderr on cleanup or mining failures instead of failing silently.
- The `.mempalace-cache` path is preferred over tmpfs or an in-memory handoff because MemPalace replaces old drawers by stable `source_file`, which requires the cache path to survive across separate hook invocations.
- Wing names are derived from the workspace basename plus a short hash of the resolved workspace path, which avoids collisions between different projects that share the same folder name.
- The hook keeps one explicit titled long-form drawer in room `chat_transcript_full`. This is fallback data, migration data, future-proof data, long-form data, safer verbatim data, and rebuildable data.
- That explicit long-form record uses its own `source_file` identity (`transcript.full.raw`) so MemPalace re-mines of `transcript.txt` do not purge it.
- The explicit long-form drawer content starts with a strong title block derived from the session folder name so it is obvious in previews and exports what the record is.
- The hook still runs `mempalace mine --mode convos --extract exchange` for the normal upstream conversation-ingest path on `transcript.txt`.
- The exported transcript path is stable per session so re-mining refreshes the same source instead of creating unrelated duplicates.
- The mining step is handled by MemPalace itself rather than by an agent-authored prompt workflow.

