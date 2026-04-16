# mempalace-copilot-hooks

Small standalone repo for the VS Code side of the current MemPalace setup.

This repo is not the MemPalace engine. It is the integration layer around it for Github Copilot use:

- Copilot hook config
- transcript export hook script
- Copilot instruction file
- minimal VS Code config snippets
- install notes for the MemPalace package currently in use

## What this solves

The fragile part was asking the model itself to be the primary verbatim recorder.
This setup moves raw chat capture into deterministic VS Code hooks instead.
The hook reads the Copilot transcript file and upserts the full session transcript directly into MemPalace.

## Included files

- `hooks/export-events.json`: active hook mapping for `UserPromptSubmit`, `PreCompact`, and `Stop`
- `hooks/export_chat_hook.py`: direct transcript-to-MemPalace upsert script
- `instructions/mempalace.instructions.md`: simplified instruction file that assumes hooks own raw capture
- `examples/settings.json`: minimal VS Code user settings snippet for hook discovery
- `examples/mcp.json`: minimal VS Code MCP server snippet
- `docs/install-mempalace.md`: package install guidance and fork decision notes

## Current tested package

The local machine this was extracted from currently uses:

- package: `mempalace-copilot==0.1.0a0`
- repo: https://github.com/crowdedLeopard/mempalace
- upstream base: https://github.com/milla-jovovich/mempalace

Short version: if you want the least friction for Copilot/VS Code, use the `crowdedLeopard` fork for now. The current hook script was validated against that package, not against upstream.

## Quick install outline

```bash
python3 -m venv "$HOME/.venvs/mempalace-copilot"
source "$HOME/.venvs/mempalace-copilot/bin/activate"
pip install --upgrade pip
pip install git+https://github.com/crowdedLeopard/mempalace.git
```

Then:

1. Copy `hooks/export-events.json` and `hooks/export_chat_hook.py` into `$HOME/.config/Code/User/copilot-hooks/`.
2. Merge `examples/settings.json` into VS Code user settings.
3. Merge `examples/mcp.json` into VS Code user `mcp.json`.
4. Place `instructions/mempalace.instructions.md` where you want Copilot to load it from.
5. Reload VS Code.

If your VS Code MCP config does not expand `$HOME` in the `command` field, replace it with your full local path.

Full details are in `docs/install-mempalace.md`.

## Behavioral notes

- The hook does not keep its own log files.
- It reads the transcript file path provided by VS Code and writes straight to MemPalace.
- Each chat session gets one rolling `chat-transcript` drawer.
- Drawer IDs are human-readable and include a short stable hash suffix to avoid collisions.

