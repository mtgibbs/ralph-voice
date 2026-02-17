"""RalphVoiceApp — Textual TUI for Ralph Voice."""

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

from audio_loop import AudioEvent, AudioLoop, EventType
from ui.messages import AudioEventMessage
from ui.widgets import AgentPanel, StatusBar


class QuitConfirmScreen(ModalScreen[str]):
    """Modal asking what to do about running ralph containers on quit."""

    DEFAULT_CSS = """
    QuitConfirmScreen {
        align: center middle;
    }
    #quit-dialog {
        width: 60;
        height: auto;
        max-height: 20;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #quit-dialog Label {
        width: 100%;
        margin-bottom: 1;
    }
    #quit-buttons {
        width: 100%;
        height: auto;
        layout: horizontal;
    }
    #quit-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, containers: list[str]) -> None:
        super().__init__()
        self.containers = containers

    def compose(self) -> ComposeResult:
        with Static(id="quit-dialog"):
            names = ", ".join(self.containers)
            yield Label(
                f"[bold yellow]Warning:[/] {len(self.containers)} ralph agent container(s) "
                f"still running:\n[dim]{names}[/]\n"
            )
            with Static(id="quit-buttons"):
                yield Button("Stop & Quit", variant="error", id="stop-quit")
                yield Button("Quit (leave running)", variant="warning", id="leave-quit")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


class RalphVoiceApp(App):
    """Textual app wrapping AudioLoop with visual feedback."""

    TITLE = "Ralph Voice"
    SUB_TITLE = "v0.1.0"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("ctrl+m", "toggle_mute", "Mute", show=True),
        Binding("ctrl+g", "toggle_panel", "Agents", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+t", "toggle_dark", "Theme", show=True),
    ]

    def __init__(self, video_mode: str = "none", start_muted: bool = False) -> None:
        super().__init__()
        self._start_muted = start_muted
        self.audio_loop = AudioLoop(
            video_mode=video_mode,
            event_callback=self._on_audio_event,
            start_muted=start_muted,
        )
        self._log_file: Optional[object] = None
        self._session_start = datetime.now()

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusBar()
        yield RichLog(id="transcript", wrap=True, highlight=True, markup=True, min_width=0)
        yield AgentPanel(mcp_client=self.audio_loop.mcp_client, id="agent-panel")
        yield Input(placeholder="Type a message (or just speak)...")
        yield Footer()

    def on_mount(self) -> None:
        self._open_log_file()
        if self._start_muted:
            self.query_one(StatusBar).muted = True
        self.run_worker(self._run_audio_loop(), exclusive=True, thread=False)
        self._write_transcript(f"[dim]Debug: app.size={self.size}, screen.size={self.screen.size}[/]")
        self._write_transcript("[dim]Starting Ralph Voice...[/]")

    async def _run_audio_loop(self) -> None:
        """Run AudioLoop as an async Textual worker."""
        try:
            await self.audio_loop.run()
        except Exception as e:
            self._write_transcript(f"[red bold]Audio loop error:[/] {e}")

    def _on_audio_event(self, event: AudioEvent) -> None:
        """Callback from AudioLoop — posts message into Textual's event system."""
        self.post_message(AudioEventMessage(event))

    def on_audio_event_message(self, message: AudioEventMessage) -> None:
        """Route AudioLoop events to the appropriate widgets."""
        event = message.event
        status_bar = self.query_one(StatusBar)
        now = datetime.now().strftime("%H:%M:%S")

        match event.type:
            case EventType.MIC_READY:
                status_bar.state = "listening"
                self._write_transcript(f"[dim]{now}[/] [blue]\\[mic][/] {event.text}")

            case EventType.GEMINI_CONNECTED:
                status_bar.state = "listening"
                self._write_transcript(f"[dim]{now}[/] [green]\\[connected][/] Gemini Live session active")

            case EventType.GEMINI_TEXT:
                self._write_transcript(f"[dim]{now}[/] [magenta]\\[gemini][/] {event.text}")

            case EventType.TOOL_CALL_START:
                status_bar.state = "thinking"
                name = event.data.get("name", "")
                args = event.data.get("args", {})
                args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
                self._write_transcript(f"[dim]{now}[/] [yellow]\\[tool][/] {name}({args_str})")
                # Track project for agent panel
                if name in ("ralph_status", "ralph_launch", "ralph_changes", "ralph_stop", "ralph_logs"):
                    project_dir = args.get("project_dir", "")
                    if project_dir:
                        self.query_one(AgentPanel).track_project(project_dir)

            case EventType.TOOL_CALL_RESULT:
                name = event.data.get("name", "")
                result_text = event.text
                if len(result_text) > 200:
                    result_text = result_text[:200] + "..."
                self._write_transcript(f"[dim]{now}[/] [green]\\[result][/] {result_text}")
                # Piggyback: instant refresh of agent panel on status/changes results
                if name in ("ralph_status", "ralph_changes", "ralph_launch"):
                    self.query_one(AgentPanel).refresh_now()

            case EventType.TOOL_CALL_ERROR:
                name = event.data.get("name", "")
                self._write_transcript(f"[dim]{now}[/] [red]\\[error][/] {name}: {event.text}")

            case EventType.PLAYBACK_START:
                status_bar.state = "speaking"

            case EventType.PLAYBACK_END:
                status_bar.state = "listening"

            case EventType.MUTE_CHANGED:
                muted = event.data.get("muted", False)
                status_bar.muted = muted
                label = "MUTED" if muted else "LIVE"
                self._write_transcript(f"[dim]{now}[/] [blue]\\[mic][/] Microphone {label}")

            case EventType.USER_TEXT:
                self._write_transcript(f"[dim]{now}[/] [bold]\\[you][/] {event.text}")

            case EventType.ERROR:
                self._write_transcript(f"[dim]{now}[/] [red bold]\\[error][/] {event.text}")

            case EventType.INFO:
                self._write_transcript(f"[dim]{now}[/] [blue dim]\\[info][/] {event.text}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle text input from the user."""
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        await self.audio_loop.send_text_message(text)

    def action_toggle_mute(self) -> None:
        """Toggle mic mute via keybinding."""
        self.audio_loop.toggle_mute()

    def action_toggle_panel(self) -> None:
        """Toggle agent panel visibility."""
        panel = self.query_one(AgentPanel)
        panel.display = not panel.display

    def action_quit(self) -> None:
        """Check for running containers, then shut down."""
        containers = self._get_running_ralph_containers()
        if containers:
            self.push_screen(QuitConfirmScreen(containers), self._on_quit_confirm)
        else:
            self._do_quit()

    def _on_quit_confirm(self, result: str) -> None:
        """Handle quit confirmation modal result."""
        if result == "stop-quit":
            containers = self._get_running_ralph_containers()
            if containers:
                self._write_transcript("[yellow]Stopping ralph containers...[/]")
                self._stop_ralph_containers(containers)
                self._write_transcript("[green]Containers stopped.[/]")
            self._do_quit()
        elif result == "leave-quit":
            self._do_quit()
        # "cancel" — do nothing, return to app

    def _do_quit(self) -> None:
        """Actually shut down and exit."""
        self.audio_loop.shutdown()
        self._close_log_file()
        self.exit()

    @staticmethod
    def _get_running_ralph_containers() -> list[str]:
        """Return names of running ralph containers."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=ralph-", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
            return [n for n in result.stdout.strip().split("\n") if n]
        except Exception:
            return []

    @staticmethod
    def _stop_ralph_containers(containers: list[str]) -> None:
        """Stop and remove the given containers."""
        try:
            subprocess.run(
                ["docker", "stop", "-t", "15"] + containers,
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["docker", "rm"] + containers,
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    def _write_transcript(self, markup: str) -> None:
        """Write a line to both the RichLog widget and the log file."""
        try:
            transcript = self.query_one("#transcript", RichLog)
            transcript.write(markup)
        except Exception:
            pass
        self._log_to_file(markup)

    # --- Log file persistence ---

    def _open_log_file(self) -> None:
        """Create a session log file in logs/."""
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        timestamp = self._session_start.strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"session-{timestamp}.log"
        self._log_file = open(log_path, "w", encoding="utf-8")
        self._log_file.write(f"# Ralph Voice session — {self._session_start.isoformat()}\n")
        self._log_file.flush()

    def _log_to_file(self, text: str) -> None:
        """Append a line to the session log, stripping Rich markup."""
        if not self._log_file:
            return
        plain = re.sub(r"\[/?[^\]]*\]", "", text)
        self._log_file.write(plain + "\n")
        self._log_file.flush()

    def _close_log_file(self) -> None:
        if self._log_file:
            self._log_file.close()
            self._log_file = None
