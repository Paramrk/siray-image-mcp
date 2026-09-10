# Connecting this MCP server to any agent

The server speaks standard MCP over stdio, so any MCP-capable client can run it. Only the
config file location and JSON/TOML shape differ.

## Use these two values everywhere

```
command:  D:\Work Project\imagegenerator\.venv\Scripts\python.exe
args:     ["D:\\Work Project\\imagegenerator\\server.py"]
```

**Use the absolute path to the venv's python, not `uv`.** GUI-launched agents (Antigravity,
Cursor, Windsurf, Claude Desktop) do not inherit your shell's PATH, so `uv` is usually not
found and the server silently fails to start. Antigravity's own docs say to use absolute
command paths. Verified here: the server starts with `PATH=C:\Windows\System32` only, from
an unrelated working directory, and still finds the API key — every path it needs is derived
from the server file's own location.

In JSON, backslashes must be doubled (`D:\\Work Project\\...`). The single-backslash form is
the most common reason a pasted config fails.

No API key goes in any of these files — it stays in `.env` next to `server.py`.

---

## On this machine

Detected as installed: **Codex**, **Antigravity**, **Cursor**, **Claude Desktop**.
(Windsurf, VS Code `mcp.json` and Gemini CLI `settings.json` were not found — their sections
below still apply if you install them.)

Existing servers in those files, which any edit must preserve:

| client | config | already has |
|---|---|---|
| Codex | `C:\Users\A\.codex\config.toml` | supabase, Neon, node_repl, browsermcp, browser-mcp |
| Antigravity | `C:\Users\A\.gemini\config\mcp_config.json` | stitch, supabase |
| Cursor | `C:\Users\A\.cursor\mcp.json` | none |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | no `mcpServers` key yet — add one |

Add the entry from each section below; **merge into the existing block, don't replace it**,
or you will drop the servers listed above. For Codex, append the `[mcp_servers.siray]` table
at the end of the file.

## Claude Code

Already registered. To redo it:

```bash
claude mcp add --scope user siray -- "D:\Work Project\imagegenerator\.venv\Scripts\python.exe" "D:\Work Project\imagegenerator\server.py"
```

`--scope user` makes it available in every project. Restart the session after adding.

## Codex CLI

`~/.codex/config.toml` (TOML, and the key is `mcp_servers` with an underscore):

```toml
[mcp_servers.siray]
command = "D:\\Work Project\\imagegenerator\\.venv\\Scripts\\python.exe"
args = ["D:\\Work Project\\imagegenerator\\server.py"]
```

## Antigravity

IDE: agent side panel → `...` → MCP Servers → Manage MCP Servers → View raw config.
File is `~/.gemini/config/mcp_config.json` globally, or `.agents/mcp_config.json` in the
workspace.

```json
{
  "mcpServers": {
    "siray": {
      "command": "D:\\Work Project\\imagegenerator\\.venv\\Scripts\\python.exe",
      "args": ["D:\\Work Project\\imagegenerator\\server.py"]
    }
  }
}
```

## Cursor

`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` globally. Same shape as
Antigravity above (`mcpServers`).

## Windsurf

`~/.codeium/windsurf/mcp_config.json`. Same shape as Antigravity above (`mcpServers`).

## Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json`. Same shape as Antigravity above
(`mcpServers`). Fully quit and reopen the app — reloading the window is not enough.

## VS Code / GitHub Copilot

`.vscode/mcp.json`. **VS Code is the odd one out**: the top-level key is `servers`, not
`mcpServers`, and each entry needs an explicit `type`. Pasting a Cursor config here fails
silently — the server just never appears.

```json
{
  "servers": {
    "siray": {
      "type": "stdio",
      "command": "D:\\Work Project\\imagegenerator\\.venv\\Scripts\\python.exe",
      "args": ["D:\\Work Project\\imagegenerator\\server.py"]
    }
  }
}
```

## Gemini CLI

`~/.gemini/settings.json`, under a top-level `mcpServers` key, same entry shape.

---

## If it doesn't show up

1. **Restart the client completely.** Nearly every MCP client reads its config only at
   startup.
2. **Check the command works on its own.** Starting the server with no client just waits
   silently on stdin, which tells you nothing — use this instead. It proves the python path,
   the dependencies, and the API key all resolve:
   ```
   "D:\Work Project\imagegenerator\.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'D:\Work Project\imagegenerator'); import server; print('OK:', len(server.core.list_models()), 'models')"
   ```
   Expect `OK: 53 models`. A `ModuleNotFoundError` means the path is wrong; a `SIRAY_API_KEY`
   error means `.env` is missing or empty.
3. **Wrong top-level key** — `servers` for VS Code, `mcpServers` for everyone else,
   `mcp_servers` for Codex TOML.
4. **Single backslashes in JSON.** Double them.
5. **Moved or rebuilt the project?** The venv path is absolute; re-run `uv sync` and update
   the configs.

## What was actually verified

- Starts and completes the MCP handshake with no `uv`, a stripped PATH, and a foreign cwd.
- Protocol version 2025-11-25, three tools listed, live API reachable in that state.
- stdout carries only valid JSON-RPC across a handshake, `tools/list` and a networked tool
  call — the httpx logs go to stderr, where they belong. A stray `print()` to stdout would
  corrupt the stream and make the server appear to connect and then die.
- Argument shapes weaker agents send: palette as a proper array, as a JSON string, as a bare
  string, and comma-separated; `tileable` and `seed` as strings. All accepted.
- Not verified: the actual Antigravity/Codex/Cursor UIs, which are not installed here. The
  config shapes come from each tool's current docs, and the protocol behaviour above is
  client-independent.
