"""Custom Textual widgets for Ralph Voice TUI."""

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

if TYPE_CHECKING:
    from mcp_handler import MCPClient


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


class AgentPanel(Widget):
    """Persistent bottom panel showing live agent status via MCP polling."""

    status_data: reactive[dict] = reactive({}, layout=True)

    _POLL_ACTIVE = 10.0
    _POLL_IDLE = 30.0
    _STALE_TIMEOUT = 30.0  # seconds to keep showing last data after containers vanish

    # Spinner frames for active agents
    _SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, mcp_client: "MCPClient", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mcp_client = mcp_client
        self._tracked_projects: set[str] = set()
        self._container_projects: dict[str, str] = {}  # container_name → project_dir
        self._container_roles: dict[str, str] = {}  # container_name → role
        self._poll_interval = self._POLL_IDLE
        self._last_nonempty: float = 0.0  # timestamp of last poll with containers
        self._frame: int = 0
        self._poll_timer = None
        self._spin_timer = None

    def on_mount(self) -> None:
        self._poll_timer = self.set_interval(self._poll_interval, self._poll_wrapper)
        self._spin_timer = self.set_interval(1 / 4, self._tick_spinner)
        # Also do an initial docker check to pick up containers already running
        self.run_worker(self._discover_from_docker(), exclusive=False, thread=False)

    def _tick_spinner(self) -> None:
        if self.status_data:
            self._frame += 1
            self.refresh()

    def _poll_wrapper(self) -> None:
        self.run_worker(self._poll(), exclusive=True, thread=False)

    async def _poll(self) -> None:
        """Poll ralph_status for each tracked project."""
        # Also check docker for containers we might not know about
        await self._discover_from_docker()

        if not self._tracked_projects:
            if self.status_data:
                # Check staleness
                if time.monotonic() - self._last_nonempty > self._STALE_TIMEOUT:
                    self.status_data = {}
            self._adjust_interval(self._POLL_IDLE)
            return

        merged: dict[str, Any] = {}
        for project_dir in list(self._tracked_projects):
            try:
                result = await self._mcp_client.call_tool(
                    "ralph_status", {"project_dir": project_dir}
                )
                text = self._extract_text(result)
                if text:
                    data = json.loads(text)
                    project_name = Path(project_dir).name
                    merged[project_name] = data
            except Exception:
                # MCP not connected yet or tool error — skip silently
                pass

        if merged:
            has_containers = any(
                d.get("containers") for d in merged.values()
            )
            if has_containers:
                self._last_nonempty = time.monotonic()
                self._adjust_interval(self._POLL_ACTIVE)
            else:
                self._adjust_interval(self._POLL_IDLE)
            self.status_data = merged
        else:
            if time.monotonic() - self._last_nonempty > self._STALE_TIMEOUT:
                self.status_data = {}
            self._adjust_interval(self._POLL_IDLE)

    def _adjust_interval(self, new_interval: float) -> None:
        if new_interval != self._poll_interval:
            self._poll_interval = new_interval
            if self._poll_timer:
                self._poll_timer.stop()
            self._poll_timer = self.set_interval(new_interval, self._poll_wrapper)

    async def _discover_from_docker(self) -> None:
        """Check docker ps for ralph containers and map them to projects."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "--filter", "name=ralph-",
                "--format", '{{.Names}}\t{{.Label "ralph.project_dir"}}\t{{.Label "ralph.role"}}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                name = parts[0] if len(parts) > 0 else ""
                project_dir = parts[1] if len(parts) > 1 else ""
                role = parts[2] if len(parts) > 2 else ""
                if name and project_dir:
                    self._tracked_projects.add(project_dir)
                    self._container_projects[name] = project_dir
                    if role:
                        self._container_roles[name] = role
        except Exception:
            pass

    def track_project(self, project_dir: str) -> None:
        """Register a project directory for polling."""
        if project_dir and project_dir not in self._tracked_projects:
            self._tracked_projects.add(project_dir)
            # Trigger an immediate poll
            self.run_worker(self._poll(), exclusive=True, thread=False)

    def refresh_now(self, data: dict | None = None) -> None:
        """Trigger an immediate refresh, optionally with inline data."""
        if data:
            try:
                parsed = json.loads(self._extract_text(data)) if not isinstance(data, dict) else data
                project = parsed.get("project", "")
                if project:
                    name = Path(project).name
                    merged = dict(self.status_data)
                    merged[name] = parsed
                    self.status_data = merged
                    self._last_nonempty = time.monotonic()
                    return
            except Exception:
                pass
        # Fallback: trigger a poll
        self.run_worker(self._poll(), exclusive=True, thread=False)

    @staticmethod
    def _extract_text(result: Any) -> str | None:
        """Pull text out of an MCP result object."""
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            for block in result:
                if hasattr(block, "text"):
                    return block.text
        if hasattr(result, "content"):
            for block in result.content:
                if hasattr(block, "text"):
                    return block.text
        return None

    def render(self) -> str:
        if not self.status_data:
            return "[dim]─ Agents ──── No agents running ─[/]"

        lines: list[str] = []
        spinner = self._SPINNER[self._frame % len(self._SPINNER)]

        for project_name, data in self.status_data.items():
            project_dir = data.get("project", "")
            all_containers = data.get("containers", [])
            # Filter containers to only those belonging to this project
            containers = [
                c for c in all_containers
                if self._container_projects.get(c.get("name", ""), project_dir) == project_dir
            ]
            stories = data.get("stories", {})
            progress = stories.get("progress", "")
            recent = data.get("recent_commits", [])

            # Header line with progress bar
            done = stories.get("done", [])
            available = stories.get("available", [])
            claimed = stories.get("claimed", [])
            verifying = stories.get("verifying", [])
            total = len(done) + len(available) + len(claimed) + len(verifying)

            bar = self._progress_bar(len(done), len(verifying), len(claimed), total)
            stale = not containers
            dim_open = "[dim]" if stale else ""
            dim_close = "[/]" if stale else ""

            lines.append(
                f"{dim_open}─ Agents: [bold]{project_name}[/bold] ──── "
                f"{progress} {bar} ─{dim_close}"
            )

            # Agent rows
            for c in containers:
                name = c.get("name", "?")
                running_for = c.get("running_for", "")
                # Match container name to claimed_by — container is "ralph-agent-1",
                # claimed_by is "agent-1". Check both exact and stripped prefix.
                agent_id = name.removeprefix("ralph-")
                agent_story = ""
                for s in claimed:
                    cb = s.get("claimed_by") or ""
                    if cb == name or cb == agent_id:
                        agent_story = f'[yellow]{s["id"]}[/yellow] [italic]"{s["title"]}"[/italic]'
                        break
                if not agent_story:
                    for s in verifying:
                        vby = s.get("verified_by") or ""
                        if vby == name or vby == agent_id:
                            agent_story = f'[cyan]{s["id"]} "{s["title"]}"[/cyan]'
                            break
                if not agent_story:
                    # Check if this agent has a done story (just finished)
                    for s in done:
                        cb = s.get("claimed_by") or ""
                        if cb == name or cb == agent_id:
                            agent_story = f'[green]{s["id"]}[/green] [dim]"{s["title"]}" done[/dim]'
                            break
                if not agent_story:
                    agent_story = "[dim]idle[/dim]"

                # Role tag from docker label
                role = self._container_roles.get(name, "")
                role_tag = f"[dim]{role[0]}[/dim] " if role else ""

                # Elapsed — extract just the time part from "5 minutes ago"
                elapsed = running_for.replace(" ago", "") if running_for else ""

                lines.append(
                    f"  {role_tag}{name:<14} {agent_story:<42} [yellow]{spinner}[/yellow] {elapsed}"
                )

            # Last commit
            if recent:
                last = recent[0]
                if len(last) > 60:
                    last = last[:57] + "..."
                lines.append(f"  [dim]last: {last}[/dim]")

        return "\n".join(lines)

    @staticmethod
    def _progress_bar(done: int, verifying: int, claimed: int, total: int) -> str:
        """Render a compact text progress bar."""
        if total == 0:
            return ""
        width = 12
        done_w = round(done / total * width)
        verify_w = round(verifying / total * width)
        claimed_w = round(claimed / total * width)
        empty_w = width - done_w - verify_w - claimed_w
        if empty_w < 0:
            empty_w = 0

        return (
            f"[green]{'█' * done_w}[/]"
            f"[cyan]{'▓' * verify_w}[/]"
            f"[yellow]{'░' * claimed_w}[/]"
            f"[dim]{'░' * empty_w}[/]"
        )
