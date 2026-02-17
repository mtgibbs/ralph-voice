"""Custom Textual widgets for Ralph Voice TUI."""

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    """Displays current voice state and mic status with animation."""

    state: reactive[str] = reactive("idle")
    muted: reactive[bool] = reactive(False)
    _frame: reactive[int] = reactive(0)

    # Animation frames per state
    _LISTENING_FRAMES = ["[green]●[/]", "[green dim]●[/]"]
    _THINKING_FRAMES = ["[yellow]⠋[/]", "[yellow]⠙[/]", "[yellow]⠹[/]", "[yellow]⠸[/]", "[yellow]⠼[/]", "[yellow]⠴[/]", "[yellow]⠦[/]", "[yellow]⠧[/]", "[yellow]⠇[/]", "[yellow]⠏[/]"]
    _SPEAKING_FRAMES = ["[cyan]≈[/]", "[cyan]~[/]", "[cyan]≋[/]", "[cyan]~[/]"]
    _IDLE_INDICATOR = "[dim]○[/]"

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / 4, self._tick)

    def _tick(self) -> None:
        self._frame += 1

    def render(self) -> str:
        indicator = self._get_indicator()
        state_label = self._get_state_label()
        mic_label = self._get_mic_label()
        return f" {indicator} {state_label}    {mic_label}"

    def _get_indicator(self) -> str:
        if self.muted and self.state == "listening":
            return self._IDLE_INDICATOR
        if self.state == "listening":
            frames = self._LISTENING_FRAMES
        elif self.state == "thinking":
            frames = self._THINKING_FRAMES
        elif self.state == "speaking":
            frames = self._SPEAKING_FRAMES
        else:
            return self._IDLE_INDICATOR
        return frames[self._frame % len(frames)]

    def _get_state_label(self) -> str:
        if self.muted and self.state == "listening":
            return "[dim]PAUSED[/]"
        labels = {
            "idle": "[dim]IDLE[/]",
            "listening": "[green bold]LISTENING[/]",
            "thinking": "[yellow bold]THINKING[/]",
            "speaking": "[cyan bold]SPEAKING[/]",
        }
        return labels.get(self.state, "[dim]IDLE[/]")

    def _get_mic_label(self) -> str:
        if self.muted:
            return "[red bold]MIC: MUTED[/]"
        return "[green]MIC: LIVE[/]"
