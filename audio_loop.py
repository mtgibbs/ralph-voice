"""AudioLoop — audio/Gemini/MCP engine extracted from main.py.

Streams mic audio to Gemini Live API, receives spoken responses, dispatches
MCP tool calls. UI-agnostic: emits AudioEvent dataclasses via a callback
instead of printing directly.
"""

import asyncio
import json
import sys
import traceback
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

import pyaudio
from dotenv import load_dotenv

from google import genai
from google.genai import types

from mcp_handler import MCPClient
from schema import mcp_tool_to_gemini

load_dotenv()

if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup

    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

# Audio constants
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = "models/gemini-2.5-flash-native-audio-latest"

SYSTEM_INSTRUCTION = """\
You are Ralph Voice, a hands-free voice assistant for managing Ralph parallel \
agents. Ralph is a system that launches multiple Claude Code agents in Docker \
containers to work on software projects in parallel.

You have access to Ralph MCP tools:
- ralph_launch: Start agents against a project directory. Accepts optional \
verifiers parameter (number of verifier agents that independently run tests to \
gate story completion, default 0)
- ralph_stop: Gracefully stop running agents
- ralph_status: Check container health, story progress, recent commits
- ralph_changes: Get only what changed since the last check (new commits, story \
transitions, new logs, container changes). For follow-up status checks, prefer \
this over ralph_status — it returns only deltas. The since_commit parameter is \
auto-injected from previous calls, so you don't need to provide it.
- ralph_logs: Read agent iteration logs
- ralph_prd_read: Read the PRD (product requirements document) with stories
- ralph_prd_update: Add, edit, or remove stories in the PRD

When the user mentions "my canvas project" or "canvas", the project directory \
is /Users/mtgibbs/dev/school-canvas-claude-integration.

When the user mentions "smoke test", the project directory \
is /Users/mtgibbs/ai-research/ralph-smoke-test.

You can also search the web using Google Search to research topics, look up \
documentation, or find current information. Use search when discussing features \
that need research or when the user asks you to look something up.

Keep spoken responses concise — this is a voice interface. Summarize tool \
results rather than reading raw JSON. For status checks, focus on: how many \
agents are running, story progress, and any issues.
"""

DEFAULT_MODE = "none"


class EventType(Enum):
    """Types of events emitted by AudioLoop."""
    MIC_READY = auto()
    GEMINI_CONNECTED = auto()
    GEMINI_TEXT = auto()
    TOOL_CALL_START = auto()
    TOOL_CALL_RESULT = auto()
    TOOL_CALL_ERROR = auto()
    PLAYBACK_START = auto()
    PLAYBACK_END = auto()
    MUTE_CHANGED = auto()
    USER_TEXT = auto()
    ERROR = auto()
    INFO = auto()


@dataclass
class AudioEvent:
    """Event emitted by AudioLoop for UI consumption."""
    type: EventType
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class AudioLoop:
    def __init__(
        self,
        video_mode: str = DEFAULT_MODE,
        event_callback: Optional[Callable[[AudioEvent], None]] = None,
        start_muted: bool = False,
    ):
        self.video_mode = video_mode
        self._event_callback = event_callback

        self.audio_in_queue: Optional[asyncio.Queue] = None
        self.out_queue: Optional[asyncio.Queue] = None

        self.session = None
        self.audio_stream = None
        self.playing = False
        self.muted = start_muted

        self.mcp_client = MCPClient()
        self._last_commits: dict[str, str] = {}

        self._pya: Optional[pyaudio.PyAudio] = None
        self._client: Optional[genai.Client] = None
        self._running = asyncio.Event()

    def _emit(self, event: AudioEvent) -> None:
        """Send event to the registered callback, if any."""
        if self._event_callback:
            self._event_callback(event)

    def toggle_mute(self) -> bool:
        """Toggle mic mute state. Returns new muted value."""
        self.muted = not self.muted
        self._emit(AudioEvent(type=EventType.MUTE_CHANGED, data={"muted": self.muted}))
        return self.muted

    async def send_text_message(self, text: str) -> None:
        """Send a text message to the Gemini session (called from UI)."""
        if not self.session:
            return
        self._emit(AudioEvent(type=EventType.USER_TEXT, text=text))
        try:
            await self.session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=text or ".")],
                ),
                turn_complete=True,
            )
        except Exception as e:
            self._emit(AudioEvent(
                type=EventType.ERROR,
                text=f"Session disconnected: {e}",
            ))

    async def handle_tool_call(self, tool_call):
        for fc in tool_call.function_calls:
            args = fc.args or {}

            # Auto-inject since_commit for ralph_changes
            if fc.name == "ralph_changes" and "since_commit" not in args:
                project_dir = args.get("project_dir", "")
                cached = self._last_commits.get(project_dir)
                if cached:
                    args["since_commit"] = cached
                    self._emit(AudioEvent(
                        type=EventType.INFO,
                        text=f"Auto-injected since_commit={cached[:8]}...",
                    ))

            self._emit(AudioEvent(
                type=EventType.TOOL_CALL_START,
                text=fc.name,
                data={"name": fc.name, "args": args},
            ))

            try:
                result = await self.mcp_client.call_tool(
                    name=fc.name,
                    arguments=args,
                )
                result_str = str(result)
                self._emit(AudioEvent(
                    type=EventType.TOOL_CALL_RESULT,
                    text=result_str,
                    data={"name": fc.name, "result": result_str},
                ))
                response_data = {"result": result}

                if fc.name in ("ralph_changes", "ralph_status"):
                    self._cache_latest_commit(args, result)
            except Exception as e:
                self._emit(AudioEvent(
                    type=EventType.TOOL_CALL_ERROR,
                    text=str(e),
                    data={"name": fc.name, "error": str(e)},
                ))
                response_data = {"error": str(e)}

            tool_response = types.LiveClientToolResponse(
                function_responses=[
                    types.FunctionResponse(
                        name=fc.name,
                        id=fc.id,
                        response=response_data,
                    )
                ]
            )
            await self.session.send_tool_response(
                function_responses=tool_response.function_responses,
            )

    def _cache_latest_commit(self, args: dict, result) -> None:
        """Extract latest_commit from tool response and cache per project."""
        try:
            text = None
            if isinstance(result, list):
                for block in result:
                    if hasattr(block, "text"):
                        text = block.text
                        break
            elif isinstance(result, str):
                text = result

            if not text:
                return

            data = json.loads(text)
            commit = data.get("latest_commit")
            project = args.get("project_dir") or data.get("project")
            if commit and project and commit != "unknown":
                self._last_commits[project] = commit
                self._emit(AudioEvent(
                    type=EventType.INFO,
                    text=f"Cached latest_commit={commit[:8]}... for {project}",
                ))
        except (json.JSONDecodeError, AttributeError):
            pass

    async def listen_audio(self):
        mic_info = self._pya.get_default_input_device_info()
        self._emit(AudioEvent(
            type=EventType.MIC_READY,
            text=mic_info["name"],
            data={"device": mic_info["name"]},
        ))
        self.audio_stream = await asyncio.to_thread(
            self._pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )
        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}
        while True:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
            if not self.playing and not self.muted:
                await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})

    async def send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def receive_audio(self):
        while True:
            turn = self.session.receive()
            async for response in turn:
                if data := response.data:
                    if not self.playing:
                        self._emit(AudioEvent(type=EventType.PLAYBACK_START))
                    self.playing = True
                    self.audio_in_queue.put_nowait(data)
                    continue
                if text := response.text:
                    self._emit(AudioEvent(type=EventType.GEMINI_TEXT, text=text))

                server_content = response.server_content
                if server_content is not None:
                    model_turn = server_content.model_turn
                    if model_turn:
                        for part in model_turn.parts:
                            if part.executable_code is not None:
                                self._emit(AudioEvent(
                                    type=EventType.GEMINI_TEXT,
                                    text=f"```python\n{part.executable_code.code}\n```",
                                ))
                            if part.code_execution_result is not None:
                                self._emit(AudioEvent(
                                    type=EventType.GEMINI_TEXT,
                                    text=f"```\n{part.code_execution_result.output}\n```",
                                ))
                    continue

                tool_call = response.tool_call
                if tool_call is not None:
                    await self.handle_tool_call(tool_call)

            # Turn complete — wait for playback to drain, then unmute mic
            while not self.audio_in_queue.empty():
                await asyncio.sleep(0.05)
            if self.playing:
                self.playing = False
                self._emit(AudioEvent(type=EventType.PLAYBACK_END))

    async def play_audio(self):
        stream = await asyncio.to_thread(
            self._pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )
        while True:
            bytestream = await self.audio_in_queue.get()
            await asyncio.to_thread(stream.write, bytestream)
            # Detect end of playback: if queue is empty after writing the last
            # chunk, unmute the mic immediately instead of waiting for Gemini's
            # turn-complete signal (which can lag several seconds).
            if self.playing and self.audio_in_queue.empty():
                await asyncio.sleep(0.08)  # brief grace for trailing chunks
                if self.audio_in_queue.empty():
                    self.playing = False
                    self._emit(AudioEvent(type=EventType.PLAYBACK_END))

    async def run(self):
        """Main entry point — connect MCP, start Gemini session, run audio tasks."""
        self._pya = pyaudio.PyAudio()
        self._client = genai.Client(http_options={"api_version": "v1alpha"})

        await self.mcp_client.connect()

        functional_tools = [
            mcp_tool_to_gemini(tool) for tool in self.mcp_client.tools
        ]
        self._emit(AudioEvent(
            type=EventType.INFO,
            text=f"Registering {len(functional_tools)} tools",
            data={"tools": [t["name"] for t in functional_tools]},
        ))

        tools = [{"google_search": {}, "function_declarations": functional_tools}]

        config = {
            "tools": tools,
            "response_modalities": ["AUDIO"],
            "system_instruction": SYSTEM_INSTRUCTION,
        }

        try:
            async with (
                self._client.aio.live.connect(model=MODEL, config=config) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                self._emit(AudioEvent(type=EventType.GEMINI_CONNECTED))

                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

                # Keep running until shutdown() is called
                await self._running.wait()
                raise asyncio.CancelledError("Shutdown requested")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            self._emit(AudioEvent(
                type=EventType.ERROR,
                text=str(EG),
            ))
            traceback.print_exception(EG)
        finally:
            if self.audio_stream:
                self.audio_stream.close()
            if self._pya:
                self._pya.terminate()
            await self.mcp_client.close()

    def shutdown(self):
        """Signal the audio loop to stop."""
        self._running.set()
