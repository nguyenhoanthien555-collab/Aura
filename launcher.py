"""
Aura desktop runtime.

    python launcher.py                  chat, with the avatar on screen
    python launcher.py --no-avatar      terminal only
    python launcher.py --voice          speak replies out loud
    python launcher.py --listen         enable the microphone
    python launcher.py --vision         let Aura see the active window
    python launcher.py --say "hello"    one turn, then exit

Command line flags override config.yaml for this run only; nothing here
writes to it. That makes `--vision` a thing you can try once without
having granted it permanently.

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
        help="override the LLM provider (mock, gemini)",
    )

    parser.add_argument(
        "--say",
        default=None,
        metavar="TEXT",
        help="send one message, print the reply and exit",
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


def main(argv=None) -> int:

    load_dotenv()

    arguments = parse_arguments(argv)

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
