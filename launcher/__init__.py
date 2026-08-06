"""
Launcher.

Turns configuration into a running companion:

    config.yaml -> build_services() -> AuraRuntime -> AuraCLI

`main.py` stays what it was in Sprint 4 - the minimal text harness.
`launcher.py` at the repository root is the desktop runtime.
"""

from launcher.services import Services, build_services
from launcher.runtime import AuraRuntime
from launcher.cli import AuraCLI

__all__ = [
    "Services",
    "build_services",
    "AuraRuntime",
    "AuraCLI",
]
