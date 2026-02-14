# Ralph Voice

Voice interface for [Ralph](https://github.com/mtgibbs/ralph) via the [Gemini Live API](https://ai.google.dev/gemini-api/docs/live-guide). Speak commands to launch, stop, and monitor parallel Claude Code agents — no keyboard needed.

Ralph Voice connects to [ralph-mcp](https://github.com/mtgibbs/ralph-mcp) over stdio and registers all 6 tools as Gemini function declarations. When you speak a command, Gemini calls the appropriate tool, gets the result, and speaks a summary back to you.

## How It Works

```
Microphone (16kHz PCM)
    │
    ▼
┌──────────────────────┐       ┌─────────────┐
│  Gemini Live API     │◄─────►│  ralph-mcp  │
│  (bidirectional      │ tool  │  (stdio)    │
│   audio stream)      │ calls │             │
└──────────────────────┘       └─────────────┘
    │
    ▼
Speaker (24kHz PCM)
```

Audio streams bidirectionally over a WebSocket. When Gemini decides to call a tool, the session pauses audio, issues the function call, waits for the result, then resumes speaking.

## Prerequisites

- **Python** 3.10+
- **[uv](https://docs.astral.sh/uv/)** (package manager)
- **PortAudio** — `brew install portaudio`
- **[1Password CLI](https://developer.1password.com/docs/cli/)** — for API key management (or use a `.env` file)
- **Gemini API key** — free from [Google AI Studio](https://aistudio.google.com/app/apikey)
- **[ralph-mcp](https://github.com/mtgibbs/ralph-mcp)** and **[ralph](https://github.com/mtgibbs/ralph)** installed locally

## Setup

```bash
git clone https://github.com/mtgibbs/ralph-voice.git
cd ralph-voice
```

### API Key (1Password)

The `.env.op` file references a 1Password secret URI. Store your Gemini API key in your vault and it gets injected at runtime — nothing on disk.

```bash
# Run with 1Password injection
op run --env-file .env.op -- uv run main.py
```

### API Key (plain .env)

If you're not using 1Password:

```bash
cp .env.example .env
# Edit .env and paste your GOOGLE_API_KEY
uv run main.py
```

## Usage

```bash
# Voice-only (default)
op run --env-file .env.op -- uv run main.py

# With camera input
op run --env-file .env.op -- uv run main.py --mode=camera

# With screen sharing
op run --env-file .env.op -- uv run main.py --mode=screen
```

Once running, just talk. You can also type at the `message >` prompt. Type `q` to quit.

### Example Commands

- "What tools do you have?"
- "Check the status of my smoke test project"
- "Launch 2 agents on my canvas project"
- "Read the PRD"
- "Stop the agents"
- "Show me the logs for agent 1"

## MCP Configuration

`mcp_config.json` defines which MCP servers to connect to. By default it points to ralph-mcp:

```json
{
  "mcpServers": {
    "ralph": {
      "command": "deno",
      "args": ["run", "-A", "/path/to/ralph-mcp/main.ts"],
      "env": {
        "RALPH_HOME": "/path/to/ralph"
      }
    }
  }
}
```

Multiple servers are supported — tools from all servers are aggregated and dispatched to the correct session automatically.

## Available Tools

| Tool | Description |
|------|-------------|
| `ralph_launch` | Start parallel agents against a project |
| `ralph_stop` | Gracefully stop running agents |
| `ralph_status` | Container health, story progress, recent commits |
| `ralph_logs` | Read agent iteration logs |
| `ralph_prd_read` | Read the PRD with story statuses |
| `ralph_prd_update` | Add, edit, or remove stories |

## Architecture

| File | Purpose |
|------|---------|
| `main.py` | AudioLoop — mic capture, Gemini Live session, speaker output, tool call dispatch |
| `mcp_handler.py` | Multi-server MCP client with tool-name-to-session routing |
| `schema.py` | Recursive MCP JSON Schema to Gemini function declaration converter |
| `mcp_config.json` | MCP server definitions |

## Cost

Gemini API free tier: 10 requests/min, 250 requests/day on Flash.

For paid usage with the native audio model:
- Audio in: ~$3/1M tokens, Audio out: ~$12/1M tokens
- A 2-minute check-in: ~$0.04–0.06
- Casual daily use (5 sessions): ~$0.30/day

## Acknowledgments

Forked from [allenbijo/gemini-live-mcp](https://github.com/allenbijo/gemini-live-mcp) (MIT license). Key changes:

- Multi-server MCP support (original hardcoded a single server)
- Proper recursive schema conversion (original dropped descriptions, enums, nested objects)
- Updated to `gemini-2.5-flash-native-audio-latest` model
- Mic muting during playback to prevent echo self-interruption
- Voice-only default mode (removed camera/screen dependencies)
- System instruction with Ralph context
