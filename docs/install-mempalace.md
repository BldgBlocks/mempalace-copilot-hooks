# Install MemPalace

This repo only packages the VS Code integration layer: hook config, hook script, instruction file, and config examples.
It does not vendor the MemPalace Python package itself.

## Which MemPalace should you use?

Current tested setup in this machine:

- installed package: `mempalace==3.3.0`
- repo: https://github.com/MemPalace/mempalace
- docs: https://mempalaceofficial.com/

Practical guidance:

- Use the official upstream package from PyPI.
- The hook script in this repo has been updated and validated against the upstream CLI shape and MCP server module.
- Official sources are the GitHub repo, the PyPI package, and `mempalaceofficial.com`.

## Recommended install

1. Create a dedicated virtual environment.
2. Install the official package into it.
3. Point VS Code MCP and hooks at that environment.

Example:

```bash
python3 -m venv "$HOME/.venvs/mempalace-copilot"
source "$HOME/.venvs/mempalace-copilot/bin/activate"
pip install --upgrade pip
pip install --upgrade mempalace
```

## Verify the package

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

## VS Code wiring

1. Put the hook files in `$HOME/.config/Code/User/copilot-hooks/`.
2. Add the hook location snippet from `examples/settings.json` to your VS Code user settings.
3. Add the MCP server snippet from `examples/mcp.json` to your VS Code user `mcp.json`.
4. Copy `instructions/mempalace.instructions.md` into the user prompts location for the profile you are using.
5. Copy `prompts/mpingest.prompt.md` into the same user prompts location if you want the `/mpingest` slash command available.
6. Reload VS Code.

For example, a desktop profile may use `~/.config/Code/User/prompts/` while a serve-web profile may use `~/.vscode-server/data/User/prompts/`.
Keeping the files in this repo under `instructions/` and `prompts/` preserves the source, but those workspace paths alone do not guarantee automatic prompt or instruction injection.

For manual hook replay, or for prompt-driven imported transcript ingest such as `/mpingest`, use the deployed MemPalace interpreter explicitly:

```bash
/home/admin/.venvs/mempalace-copilot/bin/python /home/admin/.config/Code/User/copilot-hooks/export_chat_hook.py
```

Do not rely on `python3`, `mempalace`, `uv`, or other ambient shell commands being available on `PATH` in the terminal where you run the import.

If your VS Code MCP config does not expand `$HOME` in the `command` field, replace it with the full path for your machine.

## Notes

- The hook script exports a normalized `.txt` transcript into the active workspace under `.mempalace-cache/copilot-hooks/<wing>/...` when possible, and that per-workspace cache is the intended persistent location.
- In the same session folder it also writes `transcript.full.raw`, which is filed as one explicit titled `chat_transcript_full` drawer plus deterministic closets. This explicit record is fallback data, migration data, future-proof data, long-form data, safer verbatim data, and rebuildable data.
- After filing the explicit long-form record, the hook runs `mempalace mine --mode convos --extract exchange` against the session folder so upstream MemPalace still ingests `transcript.txt` using its normal conversation logic.
- `mempalace mine --mode convos --extract general` is meant for auto-classifying conversation exports into decisions, milestones, problems, and similar memory types. It is not the right default for hook-captured Copilot transcripts when the primary requirement is raw verbatim recall.
- `mempalace mine --mode convos --extract exchange` preserves more verbatim than `general`, but it can still split a single transcript into many drawers. That is why the hook also files a separate explicit long-form drawer keyed to `transcript.full.raw`.
- The `.txt` normalization is deliberate: the VS Code hook transcript arrives as JSONL event records, and upstream MemPalace does not reliably auto-parse this exact schema as a conversation transcript. Converting it to plain `>`-marked turns gives convo mining a stable, schema-independent input.
- A stable cache path is safer than tmpfs or a pure in-memory handoff because MemPalace refreshes prior drawers by `source_file`. If the path changes on every hook run, deduplication becomes replacement-resistant and old drawers accumulate.
- The hook emits warnings to stderr when cleanup or mining fails so export-only failures are visible during debugging.
- Wing names include a short hash of the resolved workspace path to avoid collisions when different projects share the same folder basename.
- The export path is stable per session so the hook can clear previously filed explicit fallback drawers for `transcript.full.raw` and re-mine `transcript.txt` predictably.
- The hook script is deterministic and does not call Copilot or a sub-agent from Python.
- If an upstream ChromaDB change ever makes an existing palace unreadable, use `mempalace --palace /path/to/palace migrate` after making sure the palace path is mounted and reachable.
- Optional serve-web-specific preservation files live under `serve-web/`. They are examples, not the default desktop path.
