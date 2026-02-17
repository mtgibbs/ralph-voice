# Ralph Voice

Voice interface for Ralph MCP via Gemini Live API. Speak commands to launch/stop/monitor Ralph parallel agents without touching a keyboard. Features a Textual TUI with status indicators, mute control, scrollable transcript, and session logging.

## Architecture

- **main.py** — Thin launcher: parses CLI args, creates `RalphVoiceApp`, calls `app.run()`.
- **audio_loop.py** — `AudioLoop`: mic capture (16kHz PCM) → Gemini Live API → speaker output (24kHz PCM). UI-agnostic — emits `AudioEvent` dataclasses via a callback instead of printing. Handles tool call dispatch to MCP, mute control, and clean shutdown.
- **ui/app.py** — `RalphVoiceApp` (Textual `App`): composes the TUI layout, runs `AudioLoop` as an async worker, routes `AudioEventMessage`s to widgets, manages session log files.
- **ui/widgets.py** — `StatusBar`: animated state indicator (listening/thinking/speaking) and mic status (live/muted).
- **ui/messages.py** — `AudioEventMessage`: Textual `Message` wrapper for `AudioEvent`.
- **ui/styles.tcss** — Textual CSS for layout.
- **mcp_handler.py** — `MCPClient`: connects to all servers in `mcp_config.json`, aggregates tools, maps tool names to sessions for dispatch.
- **schema.py** — Converts MCP tool inputSchema (JSON Schema) to Gemini function declaration format.
- **mcp_config.json** — MCP server definitions (currently just ralph-mcp).
- **logs/** — Auto-created session transcript files (gitignored).

### Event Flow

```
AudioLoop._emit(event) → callback → app.post_message(AudioEventMessage) → widgets react
```

AudioLoop runs as a Textual async worker. PyAudio blocking calls use `asyncio.to_thread()`. Gemini's `client.aio.live.connect()` shares Textual's asyncio loop.

## Running

```bash
# Voice-only (default) — API key injected from 1Password
op run --env-file .env.op -- uv run main.py

# With camera or screen sharing
op run --env-file .env.op -- uv run main.py --mode=camera
op run --env-file .env.op -- uv run main.py --mode=screen
```

API key is stored in 1Password: `Development - Private` vault → `Google AI Studio API Keys` → `voice-api-key`. The `.env.op` template references it via `op://` URI — no secrets on disk.

### TUI Controls

- **m** — Toggle mic mute/unmute
- **q** — Quit
- **t** — Toggle dark/light theme
- Type text in the input bar and press Enter to send to Gemini

## Dependencies

- `google-genai` — Gemini API client (Live API)
- `mcp[cli]` — Model Context Protocol client
- `pyaudio` — Audio I/O (requires PortAudio: `brew install portaudio`)
- `python-dotenv` — .env file loading
- `textual` — Terminal UI framework

## Key Design Decisions

- Forked from `allenbijo/gemini-live-mcp` (MIT license)
- Event callback bridge keeps AudioLoop UI-agnostic (supports headless mode later)
- Multi-server MCP support via tool→session mapping
- Proper schema conversion instead of lossy manual extraction
- System instruction gives Gemini context about Ralph tools and known project paths
- Session transcripts persisted to `logs/session-<timestamp>.log`
- Model: `gemini-2.5-flash-native-audio-latest`
