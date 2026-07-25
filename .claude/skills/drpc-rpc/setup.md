# DRPC MCP Setup

## Get API Key

1. Go to https://drpc.org
2. Sign up (free tier: 1M compute units/month)
3. Copy API key from dashboard

## Configure MCP

After obtaining the key, run the command for your platform:

**Claude Code:**
```bash
claude mcp add --transport http drpc https://lb.drpc.org/mcp/API_KEY
```

**Codex:**
```bash
codex mcp add drpc --url https://lb.drpc.org/mcp/API_KEY
```

**Gemini CLI:**
```bash
gemini mcp add drpc https://lb.drpc.org/mcp/API_KEY -t http
```

**Pi Agent:**

Pi-specific setup lives separately under `.pi/` and is installed through the Pi extension packaged by this repository. See [.pi/INSTALL.md](../../.pi/INSTALL.md).

```bash
pi install npm:pi-mcp-adapter
pi install https://github.com/drpcorg/drpc-agent-skills
```

**Cursor** — add to `.cursor/mcp.json`:
```json
{ "mcpServers": { "drpc": { "url": "https://lb.drpc.org/mcp/API_KEY" } } }
```

**Windsurf** — add to `~/.codeium/windsurf/mcp_config.json`:
```json
{ "mcpServers": { "drpc": { "serverUrl": "https://lb.drpc.org/mcp/API_KEY" } } }
```

**Cline** — add to MCP settings:
```json
{ "mcpServers": { "drpc": { "url": "https://lb.drpc.org/mcp/API_KEY" } } }
```

**OpenClaw:**
```bash
openclaw mcp set drpc '{"url":"https://lb.drpc.org/mcp/API_KEY"}'
```

## After Setup

Restart the session for MCP tools to become available.

In the **current session**, use direct HTTP calls to execute requests without restarting — see [direct-http.md](direct-http.md).

## x402 Auto-Payment

If the agent has wallet access, it can acquire an API key automatically via the x402 protocol — no manual registration needed. See [x402-auto-key.md](x402-auto-key.md).
