"""Ralph Voice — voice interface for Ralph MCP via Gemini Live API.

Launches the Textual TUI which manages the AudioLoop, mic capture,
Gemini Live session, and MCP tool dispatch.
"""

import argparse
import os

from ui.app import RalphVoiceApp


def main():
    parser = argparse.ArgumentParser(
        description="Ralph Voice — voice control for Ralph agents"
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="none",
        help="video input mode (default: none, voice-only)",
        choices=["camera", "screen", "none"],
    )
    parser.add_argument(
        "--muted",
        action="store_true",
        help="start with microphone muted",
    )
    args = parser.parse_args()

    app = RalphVoiceApp(video_mode=args.mode, start_muted=args.muted)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        # Force exit — PyAudio threads block in asyncio.to_thread() and prevent
        # the asyncio runner's default executor from shutting down cleanly.
        # App cleanup (stream close, PyAudio terminate) already ran in _do_quit().
        os._exit(0)


if __name__ == "__main__":
    main()
