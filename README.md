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

This setup moves raw chat capture into deterministic VS Code hooks instead.
The hook reads the Copilot transcript file, writes a plain-text export into the active workspace cache, sends the normalized transcript to a remote MemPalace HTTP bridge, and lets the bridge file the explicit long-form drawer plus the normal conversation mining pass.

## Included files

- `hooks/export-events.json`: active hook mapping for `UserPromptSubmit`, `PreCompact`, and `Stop`
- `hooks/export_chat_hook.py`: transcript export script plus remote bridge submission step
- `utilities/mpimport.py`: helper for importing Markdown, `.txt`, or `.jsonl` chat exports through the deployed hook path
- `instructions/mempalace.instructions.md`: simplified instruction file that assumes hooks own raw capture
- `examples/settings.json`: minimal VS Code user settings snippet for hook discovery
- `examples/mcp.json`: minimal VS Code MCP server snippet
- `prompts/mpingest.prompt.md`: reusable slash-command prompt for ingesting imported chat exports through the deployed hook path
- `serve-web/`: sanitized bridge, shim, service, and config examples for a validated serve-web deployment path
- `docs/install-mempalace.md`: package install guidance for the official upstream package

## Current bridge target

The current setup expects the MemPalace bridge and DB to live on:

- bridge host: `10.0.0.12`
- MCP endpoint: `http://10.0.0.12:3940/mcp`
- hook ingest endpoint: `http://10.0.0.12:3940/copilot-hook`

The client machine running VS Code hooks no longer needs a local `mempalace` install. Only the bridge host needs the MemPalace package and DB.

## Quick install outline


Then:

1. Copy `hooks/export-events.json` and `hooks/export_chat_hook.py` into `$HOME/.config/Code/User/copilot-hooks/`.
2. The deployed hook writes a normalized `transcript.txt` into the active workspace under `.mempalace-cache/copilot-hooks/<wing>/...` when the workspace path is available.
3. It also writes a separate `transcript.full.raw` record in the same session folder. That local cache remains even if the bridge is unavailable.
4. Each hook event posts the normalized transcript and explicit long-form record to `http://10.0.0.12:3940/copilot-hook` by default. The bridge files the `chat_transcript_full` drawer and runs `mempalace mine --mode convos --extract exchange` on the bridge host.
5. That workspace-local cache is the intended steady-state location and is kept in place so later hook runs can rebuild the same explicit fallback record. If no workspace path is available, the hook falls back to `$HOME/.config/Code/User/copilot-hooks/exports/<wing>/...`.
6. Merge `examples/settings.json` into VS Code user settings.
7. Merge `examples/mcp.json` into VS Code user `mcp.json`.
8. Copy `instructions/mempalace.instructions.md` into an actual Copilot prompts location if you want it auto-loaded for a given profile.
9. Copy `prompts/mpingest.prompt.md` into the active user prompts directory if you want the `/mpingest` slash command available in chat.
10. Reload VS Code.

The `utilities/` folder does not get copied into the VS Code prompts or hooks folders. Leave it in your cloned repo and run those scripts from the clone path when you need them. For example, if you cloned this repo into `~/src/mempalace-copilot-hooks`, the importer stays at `~/src/mempalace-copilot-hooks/utilities/mpimport.py`.

For manual hook replay or imported transcript ingest, run the deployed hook with any working `python3` and point it at the bridge explicitly:

```bash
MEMPALACE_BRIDGE_URL=http://10.0.0.12:3940 python3 "$HOME/.config/Code/User/copilot-hooks/export_chat_hook.py"
```

The client machine does not need a working `mempalace` CLI or importable Python package anymore. It does need network reachability to the bridge.

For imported chat exports that are already `.txt` or hook-readable `.jsonl`, you can still drive the deployed hook directly one file at a time. For Markdown exports in the common heading-based format, use the repo utility so conversion and ingest stay aligned with the documented hook path:

```bash
python utilities/mpimport.py --workspace /path/to/workspace /path/to/export-or-folder
```

That command preserves the same preflight check, stable `cwd`, sequential hook execution, and summary output described in the `/mpingest` prompt while writing converted Markdown transcripts into `.mempalace-cache/imports/`.

The `serve-web/` folder documents a working browser-served VS Code path that was validated against the MemPalace HTTP bridge. It is optional, but it is no longer just speculative preservation material.

If your bridge listener moves, override the default with `MEMPALACE_BRIDGE_URL=http://host:port` in the hook command or shell environment.

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
- The hook keeps one explicit titled long-form local record and sends the same record to the bridge for filing in room `chat_transcript_full`.
- That explicit long-form record uses its own `source_file` identity (`transcript.full.raw`) so MemPalace re-mines of `transcript.txt` do not purge it.
- The explicit long-form drawer content starts with a strong title block derived from the session folder name so it is obvious in previews and exports what the record is.
- The bridge host runs `mempalace mine --mode convos --extract exchange` for the normal upstream conversation-ingest path on `transcript.txt`.
- The exported transcript path is stable per session so re-mining refreshes the same source instead of creating unrelated duplicates.
- If the bridge is unavailable or returns malformed data, the hook fails open: chat continues, local cache files remain on disk, and the hook writes a warning to stderr instead of pretending the remote ingest succeeded.
- The repo utility `utilities/mpimport.py` is a convenience wrapper around that same deployed hook path. It can convert Markdown exports into cached `transcript.txt` files, or pass `.txt` and `.jsonl` sources straight through, without bypassing the hook.

