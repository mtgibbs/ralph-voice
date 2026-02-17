"""Textual Message wrappers for AudioLoop events."""

from textual.message import Message

from audio_loop import AudioEvent


class AudioEventMessage(Message):
    """Bridges AudioLoop events into Textual's message system."""

    def __init__(self, event: AudioEvent) -> None:
        super().__init__()
        self.event = event
