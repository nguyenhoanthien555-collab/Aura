"""
Command line interface.

The chat surface for the desktop runtime. Keeps the terminal usable as a
front end while the avatar floats over the desktop, and stays entirely
separate from `main.py`, which remains the plain Sprint 4 test harness.

Slash commands are handled here, not by the brain. Asking Aura to
remember something is an instruction to the application, not a message
to the language model.
"""

import sys

from rich.console import Console

from core.logger import logger
from events.types import ListeningEvent
from tools.base import ToolRisk


console = Console()


HELP = """
[bold]Commands[/bold]
  /help                 this list
  /listen               listen once through the microphone (alias /voice)
  /say <text>           speak text out loud
  /look                 refresh and show what Aura can see
  /remember key value   store a fact about you
  /forget key           remove a fact
  /profile              show what Aura knows about you
  /tools                list permitted tools
  /run name [k=v ...]   run a tool
  /state                current avatar state
  /quit                 exit
"""


class AuraCLI:

    def __init__(self, runtime):

        self.runtime = runtime
        self.running = False

        runtime.set_tool_confirmation(self._confirm_tool)

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def run(self) -> None:

        self.running = True

        name = (self.runtime.config.get("app") or {}).get("name", "Aura")

        console.print(f"[bold cyan]{name}[/bold cyan] is awake. /help for commands.")

        while self.running:

            try:
                text = console.input("[bold green]you[/bold green] > ").strip()

            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not text:
                continue

            try:
                if text.startswith("/"):
                    self._command(text)
                else:
                    self._say(text)

            except Exception as error:
                logger.exception("Command failed: %s", error)
                console.print(f"[red]error:[/red] {error}")

        self.running = False

    def stop(self) -> None:
        self.running = False

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------

    def _say(self, text: str, source: str = "text") -> None:
        """
        One turn.

        Speech is not triggered here: the TTS engine is subscribed to
        ResponseEvent, so the reply is already on its way to the speakers
        by the time this prints it.
        """

        response = self.runtime.chat(text, source=source)

        console.print(f"[bold magenta]aura[/bold magenta] > {response.text}")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _command(self, line: str) -> None:

        parts = line[1:].split(maxsplit=1)

        name = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        handler = {
            "help": lambda _: console.print(HELP),
            "quit": self._quit,
            "exit": self._quit,
            "listen": self._voice,
            "voice": self._voice,
            "say": self._speak,
            "look": self._look,
            "remember": self._remember,
            "forget": self._forget,
            "profile": self._profile,
            "tools": self._tools,
            "run": self._run_tool,
            "state": self._state,
        }.get(name)

        if handler is None:
            console.print(f"[yellow]unknown command:[/yellow] /{name}")
            return

        handler(rest)

    def _quit(self, _rest: str) -> None:
        self.running = False

    def _voice(self, _rest: str) -> None:
        """
        Push to talk: one utterance, one reply.

        Every part of this already exists. The engine records and
        transcribes, `runtime.chat` answers, and the reply reaches the
        speakers because the TTS engine is subscribed to ResponseEvent.
        Nothing here touches audio.

        A wake word is never required here. `/listen` is explicit by
        definition, which is why KeywordWakeWord gates continuous
        listening only - stated in the `voice.stt.wake_word` comment in
        core/config.py, and left that way.
        """

        stt = self.runtime.services.stt

        if stt is None:
            console.print(
                "[yellow]voice input is disabled[/yellow] "
                "[dim](set voice.stt.enabled: true in config.yaml, "
                "or start with --listen)[/dim]"
            )
            return

        if not self._voice_is_usable(stt):
            return

        release = self._follow_listening()

        try:
            heard = self.runtime.listen()
        finally:
            release()

        if not heard:
            console.print(
                "[dim]nothing heard - try again, or raise "
                "voice.stt.record_seconds[/dim]"
            )
            return

        console.print(f"[bold green]you \\[voice][/bold green] > {heard}")

        self._say(heard, source="voice")

    @staticmethod
    def _voice_is_usable(stt) -> bool:
        """
        Whether the microphone and the recogniser can both actually run.

        Without this check, a machine with no `sounddevice` gets a mock
        microphone that returns silence, so every attempt reports "nothing
        heard" - which reads as a broken microphone rather than a missing
        package.
        """

        try:
            available = stt.is_available()

        except Exception as error:
            logger.warning("Voice input check failed: %s", error)
            available = False

        if available:
            return True

        console.print(
            "[yellow]voice input is unavailable[/yellow] "
            "[dim](no working microphone or recogniser - "
            "pip install sounddevice faster-whisper)[/dim]"
        )

        return False

    def _follow_listening(self):
        """
        Show progress while the engine works, then stop showing it.

        ListeningEvent is published around the recording, so it is the
        only thing that knows when capture ended and transcription began.
        Reading it here keeps the CLI out of the audio path instead of
        timing the two halves itself.

        Returns a callable that removes the subscription again.
        """

        bus = getattr(self.runtime, "bus", None)

        if bus is None:
            return lambda: None

        def show(event) -> None:

            if getattr(event, "active", False):
                console.print("[dim]\\[Listening...][/dim]")
            else:
                console.print("[dim]\\[Transcribing...][/dim]")

        try:
            release = bus.subscribe(ListeningEvent, show)

        except Exception as error:
            logger.warning("Could not follow the microphone: %s", error)
            return lambda: None

        def stop() -> None:

            try:
                release()
            except Exception:
                pass

        return stop

    def _speak(self, rest: str) -> None:

        if not rest:
            console.print("usage: /say <text>")
            return

        if not self.runtime.speak(rest):
            console.print("[yellow]voice output is disabled[/yellow]")

    def _look(self, _rest: str) -> None:

        vision = self.runtime.services.vision

        if vision is None:
            console.print("[yellow]vision is disabled[/yellow]")
            return

        context = vision.refresh()

        if context is None:
            console.print("[dim]nothing observed[/dim]")
            return

        console.print(f"[cyan]{context.render()}[/cyan]")

    def _remember(self, rest: str) -> None:

        parts = rest.split(maxsplit=1)

        if len(parts) < 2:
            console.print("usage: /remember <key> <value>")
            return

        if self.runtime.remember(parts[0], parts[1]):
            console.print(f"[dim]remembered {parts[0]}[/dim]")
        else:
            console.print("[yellow]profile memory is disabled[/yellow]")

    def _forget(self, rest: str) -> None:

        if not rest:
            console.print("usage: /forget <key>")
            return

        if self.runtime.forget(rest.split()[0]):
            console.print("[dim]forgotten[/dim]")
        else:
            console.print("[dim]nothing to forget[/dim]")

    def _profile(self, _rest: str) -> None:

        lines = self.runtime.profile_lines()

        if not lines:
            console.print("[dim]nothing remembered yet[/dim]")
            return

        for line in lines:
            console.print(f"  {line}")

    def _tools(self, _rest: str) -> None:

        executor = self.runtime.services.tools

        available = executor.available() if executor else []

        if not available:
            console.print(
                "[dim]no tools permitted "
                "(set tools.enabled and tools.allowed in config.yaml)[/dim]"
            )
            return

        for name in available:

            tool = executor.registry.get(name)

            console.print(f"  [bold]{name}[/bold] ({tool.risk.value}) - {tool.description}")

    def _run_tool(self, rest: str) -> None:

        if not rest:
            console.print("usage: /run <tool> [key=value ...]")
            return

        parts = rest.split()

        name = parts[0]

        arguments = {}

        for token in parts[1:]:

            if "=" not in token:
                console.print(f"[yellow]ignoring argument:[/yellow] {token}")
                continue

            key, value = token.split("=", 1)
            arguments[key] = value

        result = self.runtime.run_tool(name, arguments)

        if result.ok:
            console.print(result.output or "[dim]done[/dim]")
        else:
            console.print(f"[red]{result.error}[/red]")

    def _state(self, _rest: str) -> None:
        console.print(f"state: {self.runtime.state.value}")

    # ------------------------------------------------------------------
    # Tool approval
    # ------------------------------------------------------------------

    def _confirm_tool(self, tool, arguments: dict) -> bool:
        """
        Ask the human.

        Defaults to no on anything that is not an explicit yes, including
        EOF - a piped stdin must not become blanket approval.
        """

        if not sys.stdin or not sys.stdin.isatty():
            return False

        colour = "red" if tool.risk == ToolRisk.DANGEROUS else "yellow"

        console.print(
            f"[{colour}]Aura wants to run [bold]{tool.name}[/bold] "
            f"({tool.risk.value})[/{colour}]"
        )

        if arguments:
            console.print(f"  arguments: {arguments}")

        try:
            answer = console.input("  allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        return answer in ("y", "yes")
