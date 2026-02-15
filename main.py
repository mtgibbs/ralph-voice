"""Ralph Voice — voice interface for Ralph MCP via Gemini Live API.

Streams mic audio to Gemini, receives spoken responses, and dispatches
MCP tool calls to ralph-mcp for hands-free agent management.

Based on allenbijo/gemini-live-mcp (MIT license).
"""

import asyncio
import json
import sys
import traceback
import argparse

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
- ralph_launch: Start agents against a project directory
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

Keep spoken responses concise — this is a voice interface. Summarize tool \
results rather than reading raw JSON. For status checks, focus on: how many \
agents are running, story progress, and any issues.
"""

DEFAULT_MODE = "none"

client = genai.Client(http_options={"api_version": "v1alpha"})

pya = pyaudio.PyAudio()


class AudioLoop:
    def __init__(self, video_mode=DEFAULT_MODE):
        self.video_mode = video_mode

        self.audio_in_queue = None
        self.out_queue = None

        self.session = None
        self.audio_stream = None
        self.playing = False  # True while speaker is outputting (mutes mic)

        self.mcp_client = MCPClient()

        # Per-project commit cache for ralph_changes delta tracking
        self._last_commits: dict[str, str] = {}  # project_dir → commit hash

    async def send_text(self):
        while True:
            text = await asyncio.to_thread(input, "message > ")
            if text.lower() == "q":
                break
            await self.session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=text or ".")],
                ),
                turn_complete=True,
            )

    async def handle_tool_call(self, tool_call):
        for fc in tool_call.function_calls:
            args = fc.args or {}

            # Auto-inject since_commit for ralph_changes
            if fc.name == "ralph_changes" and "since_commit" not in args:
                project_dir = args.get("project_dir", "")
                cached = self._last_commits.get(project_dir)
                if cached:
                    args["since_commit"] = cached
                    print(f"\n[voice] Auto-injected since_commit={cached[:8]}...")

            print(f"\n[tool] {fc.name}({args})")
            try:
                result = await self.mcp_client.call_tool(
                    name=fc.name,
                    arguments=args,
                )
                print(f"[tool] result: {result}")
                response_data = {"result": result}

                # Cache latest_commit from ralph_changes or ralph_status responses
                if fc.name in ("ralph_changes", "ralph_status"):
                    self._cache_latest_commit(args, result)
            except Exception as e:
                print(f"[tool] error: {e}")
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
            # MCP result is a list of content blocks; find the text one
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
                print(f"[voice] Cached latest_commit={commit[:8]}... for {project}")
        except (json.JSONDecodeError, AttributeError):
            pass

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        print(f"Microphone: {mic_info['name']}")
        self.audio_stream = await asyncio.to_thread(
            pya.open,
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
            if not self.playing:
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
                    self.playing = True
                    self.audio_in_queue.put_nowait(data)
                    continue
                if text := response.text:
                    print(text, end="")

                server_content = response.server_content
                if server_content is not None:
                    model_turn = server_content.model_turn
                    if model_turn:
                        for part in model_turn.parts:
                            if part.executable_code is not None:
                                print(f"```python\n{part.executable_code.code}\n```")
                            if part.code_execution_result is not None:
                                print(f"```\n{part.code_execution_result.output}\n```")
                    continue

                tool_call = response.tool_call
                if tool_call is not None:
                    await self.handle_tool_call(tool_call)

            # Turn complete — wait for playback to drain, then unmute mic
            while not self.audio_in_queue.empty():
                await asyncio.sleep(0.05)
            self.playing = False

    async def play_audio(self):
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )
        while True:
            bytestream = await self.audio_in_queue.get()
            await asyncio.to_thread(stream.write, bytestream)

    async def run(self):
        # Connect to MCP servers and build tool declarations
        await self.mcp_client.connect()

        functional_tools = [
            mcp_tool_to_gemini(tool) for tool in self.mcp_client.tools
        ]
        print(f"\n[gemini] Registering {len(functional_tools)} tools")
        for t in functional_tools:
            print(f"  - {t['name']}")

        tools = [{"function_declarations": functional_tools}]

        config = {
            "tools": tools,
            "response_modalities": ["AUDIO"],
            "system_instruction": SYSTEM_INSTRUCTION,
        }

        try:
            async with (
                client.aio.live.connect(model=MODEL, config=config) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                send_text_task = tg.create_task(self.send_text())
                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

                await send_text_task
                raise asyncio.CancelledError("User requested exit")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            if self.audio_stream:
                self.audio_stream.close()
            traceback.print_exception(EG)
        finally:
            await self.mcp_client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ralph Voice — voice control for Ralph agents")
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        help="video input mode (default: none, voice-only)",
        choices=["camera", "screen", "none"],
    )
    args = parser.parse_args()
    main = AudioLoop(video_mode=args.mode)
    asyncio.run(main.run())
