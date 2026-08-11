"""
Aura desktop runtime.

    python launcher.py                  chat, with the avatar on screen
    python launcher.py --no-avatar      terminal only
    python launcher.py --voice          speak replies out loud
    python launcher.py --listen         enable the microphone
    python launcher.py --vision         let Aura see the active window
    python launcher.py --say "hello"    one turn, then exit
    python launcher.py --server         serve the HTTP/WebSocket API

Command line flags override config.yaml for this run only; nothing here
writes to it. That makes `--vision` a thing you can try once without
having granted it permanently.

`--server` is the same Aura with a different front end: no avatar, no
terminal, one HTTP process. Everything behind it - Brain, Memory,
Personality, Providers - is the code the desktop uses.

`main.py` is unchanged and remains the Sprint 4 text harness.

Naming note: this file sits next to the `launcher/` package. Python
resolves packages before modules, so `import launcher.cli` always finds
the package, while `python launcher.py` runs this file as `__main__`.
The two coexist deliberately - the brief asks for both - but nothing
should ever `import launcher` expecting to get this file.
"""

import argparse
import sys

from dotenv import load_dotenv


def parse_arguments(argv=None):

    parser = argparse.ArgumentParser(
        prog="launcher.py",
        description="Start Aura, the local AI companion.",
    )

    parser.add_argument(
        "--no-avatar",
        action="store_true",
        help="run without the floating window",
    )

    parser.add_argument(
        "--voice",
        action="store_true",
        help="speak replies out loud",
    )

    parser.add_argument(
        "--listen",
        action="store_true",
        help="enable microphone input",
    )

    parser.add_argument(
        "--vision",
        action="store_true",
        help="let Aura observe the active window",
    )

    parser.add_argument(
        "--provider",
        default=None,
        help="override the LLM provider (mock, gemini, ollama)",
    )

    parser.add_argument(
        "--say",
        default=None,
        metavar="TEXT",
        help="send one message, print the reply and exit",
    )

    parser.add_argument(
        "--server",
        action="store_true",
        help="serve the HTTP/WebSocket API instead of the desktop UI",
    )

    parser.add_argument(
        "--host",
        default=None,
        help="server bind address (default from AURA_SERVER_HOST)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="server port (default from AURA_SERVER_PORT)",
    )

    return parser.parse_args(argv)


def apply_overrides(config: dict, arguments) -> dict:
    """
    Fold command line flags into the loaded config.

    Only ever turns things on, except for --no-avatar. A flag cannot
    silently disable something the user enabled in config.yaml.
    """

    if arguments.no_avatar:
        config["avatar"]["enabled"] = False

    if arguments.voice:
        config["voice"]["tts"]["enabled"] = True

    if arguments.listen:
        config["voice"]["stt"]["enabled"] = True

    if arguments.vision:
        config["vision"]["enabled"] = True

    if arguments.provider:
        config["llm"]["provider"] = arguments.provider

    return config


def run_server(arguments) -> int:
    """
    Serve the API.

    Imported here, not at module scope: FastAPI and uvicorn are only
    needed in server mode, and a desktop user who never runs `--server`
    should not need them installed to start Aura.
    """

    try:
        import uvicorn
    except ImportError:
        print(
            "Server mode needs FastAPI and uvicorn:\n"
            "    pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    from server.config import (
        InsecureConfigurationError,
        enforce_auth_policy,
        settings,
    )

    host = arguments.host or settings.host
    port = arguments.port or settings.port

    # The same policy the ASGI lifespan enforces, applied here so that
    # `launcher.py --server` reports it as a configuration error on stderr
    # instead of as a startup traceback from inside uvicorn.
    try:
        insecure_warning = enforce_auth_policy()
    except InsecureConfigurationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if insecure_warning:
        print(f"WARNING: {insecure_warning}", file=sys.stderr)

    uvicorn.run(
        "server.main:app",
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        reload=False,
    )

    return 0


def main(argv=None) -> int:

    load_dotenv()

    arguments = parse_arguments(argv)

    if arguments.server:
        return run_server(arguments)

    from core.config import load_config
    from launcher.cli import AuraCLI
    from launcher.runtime import AuraRuntime

    config = apply_overrides(load_config(), arguments)

    runtime = AuraRuntime(config=config)

    # One shot mode: no avatar loop, no interactive session.
    if arguments.say is not None:

        runtime.start()

        try:
            print(runtime.chat(arguments.say).text)
        finally:
            runtime.stop()

        return 0

    cli = AuraCLI(runtime)

    runtime.run(cli.run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
