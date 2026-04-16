# Install MemPalace

This repo only packages the VS Code integration layer: hook config, hook script, instruction file, and config examples.
It does not vendor the MemPalace Python package itself.

## Which MemPalace should you use?

Current tested setup in this machine:

- installed package: `mempalace-copilot==0.1.0a0`
- homepage: https://github.com/crowdedLeopard/mempalace
- upstream: https://github.com/milla-jovovich/mempalace

Practical guidance:

- Use the `crowdedLeopard/mempalace` fork if you want the Copilot-focused CLI and MCP setup commands that are already wired for VS Code.
- Use upstream only if you have checked that it still exposes the same Python import path `mempalace.config.MempalaceConfig`, the same MCP module `mempalace.mcp_server`, and a compatible Chroma collection layout.
- The hook script in this repo is written against the currently installed fork and is not claimed to be upstream-compatible without verification.

## Recommended install

1. Create a dedicated virtual environment.
2. Install the Copilot fork into it.
3. Point VS Code MCP and hooks at that environment.

Example:

```bash
python3 -m venv "$HOME/.venvs/mempalace-copilot"
source "$HOME/.venvs/mempalace-copilot/bin/activate"
pip install --upgrade pip
pip install git+https://github.com/crowdedLeopard/mempalace.git
```

If the fork later ships a normal package name on PyPI and you trust that release, you can install from PyPI instead.

## Verify the package

```bash
"$HOME/.venvs/mempalace-copilot/bin/python" -m mempalace.mcp_server
```

That should start the MCP server process.
Stop it with Ctrl+C after confirming it launches.

## VS Code wiring

1. Put the hook files in `$HOME/.config/Code/User/copilot-hooks/`.
2. Add the hook location snippet from `examples/settings.json` to your VS Code user settings.
3. Add the MCP server snippet from `examples/mcp.json` to your VS Code user `mcp.json`.
4. Put the instruction file where you want Copilot to load it from.
5. Reload VS Code.

If your VS Code MCP config does not expand `$HOME` in the `command` field, replace it with the full path for your machine.

## Notes

- The hook script stores full transcript JSONL into the `mempalace_drawers` collection.
- Transcript entries are updated in place per session, not appended forever as separate drawer IDs.
- Human-readable transcript IDs are derived from session date plus first prompt, with a short stable hash suffix for uniqueness.
