# Serve-Web Deployment Notes

This folder preserves the working serve-web support files without baking machine-specific paths, usernames, ports, or storage locations into the repo.

These files are optional.
The default and simpler setup for normal desktop VS Code remains:

- hook-based transcript export and mining
- direct MemPalace MCP wiring
- the repo instruction source file copied into the active profile's prompts directory

Use this folder if you need a browser-served VS Code instance. This path was validated end to end with MemPalace MCP calls succeeding through the HTTP bridge.

## Included Files

- `mempalace_mcp_http_bridge.py`: stdio-to-HTTP MCP bridge for MemPalace
- `navigator-shim.cjs`: serve-web workaround that injects `--supportGlobalNavigator`
- `code-serve-web.service`: sanitized systemd user service example for `code-tunnel serve-web`
- `mempalace-mcp-bridge.service`: sanitized systemd user service example for the bridge
- `mcp.json`: sanitized MCP client example for the serve-web profile

## Sanitization Rules

All examples here deliberately avoid:

- absolute user-specific home paths
- machine-specific palace paths
- private hostnames
- secrets or connection tokens

Replace placeholders before deployment.

## Why The Repo Instruction File Was Not Auto-Loaded Here

The repo file at `instructions/mempalace.instructions.md` is version-controlled source material.
It is not automatically loaded into a Copilot session merely because the workspace contains it.

In the desktop profile used for this chat, there was no prompts directory at the configured user-prompts location, so there was nothing for the session bootstrap to auto-inject from user prompts.
In contrast, the serve-web profile did have a prompts directory and a deployed `mempalace.instructions.md` file.

Practical rule:

- keep the canonical instruction text in this repo
- copy it into the active profile's prompts directory when you want automatic loading

## Deployment Notes

1. Copy `instructions/mempalace.instructions.md` into the prompts directory for the profile you are actually using.
2. Adjust the placeholders in the service files and `mcp.json`.
3. Keep the bridge bound to loopback unless you explicitly want remote access and have additional protections in front of it.
4. Treat this folder as an optional serve-web deployment layer, not the canonical baseline desktop setup.