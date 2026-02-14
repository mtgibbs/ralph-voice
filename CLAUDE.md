# Ralph Voice

Voice interface for Ralph MCP via Gemini Live API. Speak commands to launch/stop/monitor Ralph parallel agents without touching a keyboard.

## Architecture

- **main.py** — AudioLoop: mic capture (16kHz PCM) → Gemini Live API → speaker output (24kHz PCM). Handles tool call responses from Gemini by dispatching to MCP.
- **mcp_handler.py** — MCPClient: connects to all servers in `mcp_config.json`, aggregates tools, maps tool names to sessions for dispatch.
- **schema.py** — Converts MCP tool inputSchema (JSON Schema) to Gemini function declaration format by stripping unsupported fields and recursing into nested objects.
- **mcp_config.json** — MCP server definitions (currently just ralph-mcp).

## Running

```bash
# Voice-only (default) — API key injected from 1Password
op run --env-file .env.op -- uv run main.py

# With camera or screen sharing
op run --env-file .env.op -- uv run main.py --mode=camera
op run --env-file .env.op -- uv run main.py --mode=screen
```

API key is stored in 1Password: `Development - Private` vault → `Google AI Studio API Keys` → `voice-api-key`. The `.env.op` template references it via `op://` URI — no secrets on disk.

Type `q` + Enter in the terminal to quit. You can also type text messages at the `message >` prompt.

## Dependencies

- `google-genai` — Gemini API client (Live API)
- `mcp[cli]` — Model Context Protocol client
- `pyaudio` — Audio I/O (requires PortAudio: `brew install portaudio`)
- `python-dotenv` — .env file loading

## Key Design Decisions

- Forked from `allenbijo/gemini-live-mcp` (MIT license)
- Removed opencv/pillow/mss deps (camera/screen code removed from default, can be re-added)
- Multi-server MCP support via tool→session mapping
- Proper schema conversion instead of lossy manual extraction
- System instruction gives Gemini context about Ralph tools and known project paths
- Model: `gemini-2.0-flash` (confirmed Live API support)
