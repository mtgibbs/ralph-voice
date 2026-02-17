"""Ralph Voice — voice interface for Ralph MCP via Gemini Live API.

Launches the Textual TUI which manages the AudioLoop, mic capture,
Gemini Live session, and MCP tool dispatch.
"""

import argparse

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
    app.run()


if __name__ == "__main__":
    main()
