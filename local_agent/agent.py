from pathlib import Path
from datetime import datetime
import sys
import time

import ollama
import yaml

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from prompts import REASONER_SYSTEM, CODER_SYSTEM


console = Console()


# ============================================================
# TELEMETRY
# ============================================================

class Telemetry:
    """
    Live high-level progress logger.

    The telemetry records:
    - current agent stage
    - model status
    - elapsed time
    - token statistics when available

    It intentionally does NOT expose private chain-of-thought.
    """

    def __init__(
        self,
        log_file: str,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.log_file = Path(log_file)

        if not self.enabled:
            return

        self.log_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Start fresh session.
        self.log_file.write_text(
            "",
            encoding="utf-8",
        )

    def log(
        self,
        icon: str,
        stage: str,
        message: str,
    ):
        if not self.enabled:
            return

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        line = (
            f"[{timestamp}] "
            f"{icon} {stage}\n"
            f"  {message}\n"
        )

        with self.log_file.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                line + "\n"
            )

        console.print(
            f"[dim][{timestamp}][/dim] "
            f"{icon} [bold]{stage}[/bold]"
        )

        console.print(
            f"  {message}"
        )


# ============================================================
# REPOSITORY READER
# ============================================================

class AURAReader:
    """
    Read-only repository reader.

    This phase intentionally has no write/edit functionality.
    """

    IGNORED_DIRS = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "site-packages",
        "vendor",
    }

    SOURCE_EXTENSIONS = {
        ".py",
        ".md",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".txt",
    }

    PRIORITY_NAMES = {
        "main.py",
        "__main__.py",
        "agent.py",
        "brain.py",
        "memory.py",
        "config.py",
        "provider.py",
        "providers.py",
        "tools.py",
        "tool.py",
        "vision.py",
        "tts.py",
        "stt.py",
    }

    PRIORITY_WORDS = {
        "agent",
        "brain",
        "memory",
        "provider",
        "tool",
        "vision",
        "tts",
        "stt",
        "config",
        "model",
        "android",
    }

    def __init__(
        self,
        root: Path,
        max_files: int,
        max_chars: int,
    ):
        self.root = root.resolve()
        self.max_files = max_files
        self.max_chars = max_chars

        if not self.root.exists():
            raise FileNotFoundError(
                f"AURA project does not exist: "
                f"{self.root}"
            )

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"AURA project path is not a directory: "
                f"{self.root}"
            )

    def _ignored(
        self,
        path: Path,
    ) -> bool:
        """
        Ignore virtual environments, caches,
        dependency trees and build artifacts.
        """

        for part in path.parts:
            lower = part.lower()

            # Exact ignored directories.
            if lower in self.IGNORED_DIRS:
                return True

            # Virtual environment variants.
            if lower.startswith(".venv"):
                return True

            if (
                lower == "venv"
                or lower.startswith("venv-")
                or lower.startswith("venv_")
            ):
                return True

            # Dependency trees.
            if "site-packages" in lower:
                return True

            # Python caches.
            if lower in {
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
            }:
                return True

        return False

    def _read(
        self,
        path: Path,
    ) -> str:

        try:
            return path.read_text(
                encoding="utf-8",
                errors="replace",
            )

        except Exception as exc:
            return (
                f"[READ ERROR: {exc}]"
            )

    def _score(
        self,
        path: Path,
    ) -> tuple:
        """
        Prioritize files likely to describe
        AURA's architecture.
        """

        name = path.name.lower()
        stem = path.stem.lower()

        relative = str(
            path.relative_to(
                self.root
            )
        ).lower()

        score = 100

        # Exact architecture files.
        if name in self.PRIORITY_NAMES:
            score -= 50

        # Architecture-related names/paths.
        for word in self.PRIORITY_WORDS:

            if word in stem:
                score -= 10

            if word in relative:
                score -= 5

        # Documentation.
        if name in {
            "readme.md",
            "claude.md",
            "architecture.md",
        }:
            score -= 30

        # Tests are useful but shouldn't dominate
        # the first architecture scan.
        if "test" in relative:
            score += 10

        return (
            score,
            len(path.parts),
            relative,
        )

    def discover_files(
        self,
    ) -> list[Path]:

        files = []

        for path in self.root.rglob("*"):

            if not path.is_file():
                continue

            if self._ignored(path):
                continue

            if (
                path.suffix.lower()
                not in self.SOURCE_EXTENSIONS
            ):
                continue

            files.append(path)

        files.sort(
            key=self._score
        )

        return files[
            : self.max_files
        ]

    def repository_tree(
        self,
        max_entries: int = 250,
    ) -> str:

        entries = []

        for path in self.root.rglob("*"):

            if self._ignored(path):
                continue

            relative = path.relative_to(
                self.root
            )

            if len(entries) >= max_entries:
                break

            kind = (
                "DIR "
                if path.is_dir()
                else "FILE"
            )

            entries.append(
                f"{kind} {relative}"
            )

        return "\n".join(
            entries
        )

    def build_context(
        self,
    ) -> str:

        files = self.discover_files()

        sections = []

        sections.append(
            f"PROJECT ROOT:\n"
            f"{self.root}"
        )

        sections.append(
            "REPOSITORY TREE:\n"
            + self.repository_tree()
        )

        sections.append(
            "SELECTED PROJECT FILES "
            f"({len(files)}):"
        )

        for path in files:

            relative = path.relative_to(
                self.root
            )

            content = self._read(
                path
            )

            if len(content) > self.max_chars:

                content = (
                    content[
                        : self.max_chars
                    ]
                    + "\n\n"
                    "[FILE TRUNCATED]"
                )

            sections.append(
                f"\n--- {relative} ---\n"
                f"{content}"
            )

        return "\n\n".join(
            sections
        )


# ============================================================
# LOCAL MODEL
# ============================================================

class LocalModel:
    """
    Ollama model wrapper with streaming telemetry.
    """

    def __init__(
        self,
        model: str,
        temperature: float,
        telemetry: Telemetry | None = None,
        heartbeat_seconds: int = 5,
    ):
        self.model = model
        self.temperature = temperature
        self.telemetry = telemetry
        self.heartbeat_seconds = (
            heartbeat_seconds
        )

    def chat(
        self,
        system: str,
        prompt: str,
    ) -> str:

        started = time.monotonic()

        last_heartbeat = started

        generated_tokens = 0

        chunks = []

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if self.telemetry:

            self.telemetry.log(
                "⚙️",
                "MODEL",
                f"{self.model} started processing...",
            )

        # ----------------------------------------------------
        # STREAM
        # ----------------------------------------------------

        try:

            stream = ollama.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                options={
                    "temperature": (
                        self.temperature
                    ),
                },
                stream=True,
            )

            for chunk in stream:

                message = chunk.get(
                    "message",
                    {},
                )

                content = message.get(
                    "content",
                    "",
                )

                if content:
                    chunks.append(
                        content
                    )

                # --------------------------------------------
                # TOKEN COUNT
                # --------------------------------------------

                token_count = (
                    chunk.get(
                        "eval_count"
                    )
                )

                if token_count is not None:
                    generated_tokens = (
                        token_count
                    )

                # --------------------------------------------
                # HEARTBEAT
                # --------------------------------------------

                now = time.monotonic()

                if (
                    self.telemetry
                    and (
                        now
                        - last_heartbeat
                        >= self.heartbeat_seconds
                    )
                ):

                    elapsed = (
                        now
                        - started
                    )

                    speed = (
                        generated_tokens
                        / elapsed
                        if elapsed > 0
                        and generated_tokens > 0
                        else 0
                    )

                    if generated_tokens:

                        message_text = (
                            f"{self.model} "
                            f"processing... "
                            f"{elapsed:.0f}s elapsed | "
                            f"{generated_tokens:,} "
                            f"tokens | "
                            f"{speed:.1f} tok/s"
                        )

                    else:

                        message_text = (
                            f"{self.model} "
                            f"processing... "
                            f"{elapsed:.0f}s elapsed"
                        )

                    self.telemetry.log(
                        "⚙️",
                        "MODEL",
                        message_text,
                    )

                    last_heartbeat = now

        except Exception:

            elapsed = (
                time.monotonic()
                - started
            )

            if self.telemetry:

                self.telemetry.log(
                    "❌",
                    "MODEL",
                    f"{self.model} failed after "
                    f"{elapsed:.1f}s.",
                )

            raise

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        result = "".join(
            chunks
        )

        elapsed = (
            time.monotonic()
            - started
        )

        if not result:

            if self.telemetry:

                self.telemetry.log(
                    "❌",
                    "MODEL",
                    f"{self.model} returned "
                    "an empty response.",
                )

            raise RuntimeError(
                f"Model {self.model} "
                "returned an empty response."
            )

        # ----------------------------------------------------
        # FINAL STATS
        # ----------------------------------------------------

        speed = (
            generated_tokens / elapsed
            if elapsed > 0
            and generated_tokens > 0
            else 0
        )

        if self.telemetry:

            if generated_tokens:

                self.telemetry.log(
                    "📊",
                    "MODEL",
                    f"{self.model} finished in "
                    f"{elapsed:.1f}s | "
                    f"{generated_tokens:,} tokens | "
                    f"{speed:.1f} tok/s",
                )

            else:

                self.telemetry.log(
                    "📊",
                    "MODEL",
                    f"{self.model} finished in "
                    f"{elapsed:.1f}s.",
                )

        return result


# ============================================================
# DUAL MODEL AGENT
# ============================================================

class DualityLocalAgent:
    """
    AURA dual-model audit agent.

    Pipeline:

        AURA repository
              |
              v
        Qwen3 8B Reasoner
              |
              v
            Audit
              |
              v
        Qwen3.5 9B Coder
              |
              v
        Independent verification

    READ-ONLY.
    """

    def __init__(
        self,
        config_path: str,
    ):

        config_file = (
            Path(config_path)
            .resolve()
        )

        if not config_file.exists():

            raise FileNotFoundError(
                f"Config not found: "
                f"{config_file}"
            )

        with config_file.open(
            "r",
            encoding="utf-8",
        ) as file:

            self.config = (
                yaml.safe_load(file)
            )

        project_config = (
            self.config["project"]
        )

        model_config = (
            self.config["models"]
        )

        runtime_config = (
            self.config["runtime"]
        )

        # ----------------------------------------------------
        # TELEMETRY
        # ----------------------------------------------------

        telemetry_config = (
            runtime_config.get(
                "telemetry",
                {},
            )
        )

        self.telemetry = Telemetry(
            log_file=telemetry_config.get(
                "log_file",
                "runtime/thinking.log",
            ),
            enabled=telemetry_config.get(
                "enabled",
                True,
            ),
        )

        heartbeat_seconds = (
            telemetry_config.get(
                "heartbeat_seconds",
                5,
            )
        )

        # ----------------------------------------------------
        # REPOSITORY
        # ----------------------------------------------------

        root = Path(
            project_config["root"]
        )

        self.reader = AURAReader(
            root=root,
            max_files=runtime_config.get(
                "max_context_files",
                12,
            ),
            max_chars=runtime_config.get(
                "max_file_chars",
                12000,
            ),
        )

        # ----------------------------------------------------
        # REASONER
        # ----------------------------------------------------

        self.reasoner = LocalModel(
            model=model_config[
                "reasoner"
            ],
            temperature=runtime_config.get(
                "temperature_reasoner",
                0.2,
            ),
            telemetry=self.telemetry,
            heartbeat_seconds=heartbeat_seconds,
        )

        # ----------------------------------------------------
        # CODER
        # ----------------------------------------------------

        self.coder = LocalModel(
            model=model_config[
                "coder"
            ],
            temperature=runtime_config.get(
                "temperature_coder",
                0.2,
            ),
            telemetry=self.telemetry,
            heartbeat_seconds=heartbeat_seconds,
        )

    # ========================================================
    # REASONER
    # ========================================================

    def audit(self) -> str:

        console.print(
            Panel(
                "🧠 Qwen3 8B\n"
                "READ-ONLY architecture audit\n\n"
                "No files will be modified.",
                title="AURA Reasoner",
            )
        )

        self.telemetry.log(
            "🧠",
            "REASONER",
            "Task: Audit AURA architecture",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Inspecting AURA project structure...",
        )

        context = (
            self.reader.build_context()
        )

        self.telemetry.log(
            "📂",
            "ANALYSIS",
            "Repository context collected.",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Inspecting project entry points...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Tracing provider and model routing...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Checking memory subsystem...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Checking tool registration and execution...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Checking vision, TTS and STT integration...",
        )

        prompt = f"""
You are auditing the AURA PROJECT.

The repository itself is the ONLY source of truth.

Do NOT modify anything.

Perform a deep architecture audit.

Answer:

1. What is AURA's current architecture?
2. What is the complete message flow from user input
   to final response?
3. Where is provider/model routing implemented?
4. Where are system prompts and personality defined?
5. How is memory stored and retrieved?
6. How are tools registered?
7. How are tools executed?
8. How does the coding assistant work?
9. How does vision work?
10. How do TTS/STT components connect to the system?
11. How does Android/accessibility integration work?
12. What is the current local-model integration?
13. Where are provider fallbacks implemented?
14. What architectural contradictions exist?
15. What are the five highest-risk issues?
16. Can local Qwen models replace the current remote
    model path safely?
17. What should NOT be changed?

IMPORTANT:

Separate your findings into:

# VERIFIED FACTS

# INFERENCES

# RISKS

# RECOMMENDED NEXT STEPS

Use exact file paths and class/function names
whenever possible.

Do not hallucinate.

CRITICAL CONTEXT RULE:

Ignore virtual environments and dependencies.

Do NOT analyze:

- .venv
- .venv-* directories
- venv
- venv-* directories
- site-packages
- pip internals
- uvicorn internals
- pydantic internals
- SQLAlchemy internals
- anyio internals
- fsspec internals
- Hugging Face package internals
- dependency implementation details

Only discuss a dependency when AURA itself explicitly
imports or relies on that dependency in a way relevant
to AURA's architecture.

Do NOT answer as if the user pasted third-party
package source code.

Your first section MUST be:

# VERIFIED FACTS

Your first verified fact MUST identify the actual
AURA entry point using evidence from the repository.

REPOSITORY:

==============================
{context}
==============================
"""

        self.telemetry.log(
            "🧠",
            "REASONER",
            f"Sending architecture audit to "
            f"{self.reasoner.model}...",
        )

        result = self.reasoner.chat(
            system=REASONER_SYSTEM,
            prompt=prompt,
        )

        self.telemetry.log(
            "🧩",
            "SYNTHESIS",
            "Architecture analysis completed. "
            "Generating audit report...",
        )

        self.telemetry.log(
            "✅",
            "COMPLETE",
            "Reasoner finished the AURA architecture audit.",
        )

        return result

    # ========================================================
    # CODER
    # ========================================================

    def verify_audit(
        self,
        audit: str,
    ) -> str:

        console.print(
            Panel(
                "🧑‍💻 Qwen3.5 9B\n"
                "READ-ONLY verification\n\n"
                "No files will be modified.",
                title="AURA Coder",
            )
        )

        self.telemetry.log(
            "🧑‍💻",
            "CODER",
            "Task: Independently verify Reasoner audit",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Re-reading relevant AURA source files...",
        )

        context = (
            self.reader.build_context()
        )

        self.telemetry.log(
            "📂",
            "ANALYSIS",
            "Repository context collected.",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Cross-checking architecture claims...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Checking provider routing...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Checking memory and tool boundaries...",
        )

        self.telemetry.log(
            "🔍",
            "ANALYSIS",
            "Checking local-model integration risks...",
        )

        prompt = f"""
You are the implementation engineer reviewing
an architecture audit of the AURA PROJECT.

You must independently verify the first engineer's
claims against the actual repository.

DO NOT MODIFY ANY FILES.

First identify:

1. VERIFIED claims.
2. INCORRECT claims.
3. UNSUPPORTED claims.
4. Important things the first engineer missed.
5. Exact files and symbols involved.
6. Safest implementation path.
7. Risks of introducing local Qwen models.
8. Required tests.

Then produce:

# IMPLEMENTATION PLAN

The plan must:

- preserve existing architecture where possible
- avoid unnecessary rewrites
- identify exact files
- identify exact classes/functions
- explain dependencies
- explain compatibility risks
- explain rollback strategy
- explain how to test the changes

Do not pretend anything was implemented.

CRITICAL RULE:

The repository is the source of truth.

Ignore third-party package internals and virtual environments.

Do NOT analyze:

- .venv
- .venv-*
- venv
- venv-*
- site-packages

unless AURA's own source explicitly depends on
a specific package behavior relevant to the question.

FIRST ENGINEER AUDIT:

==============================
{audit}
==============================

CURRENT AURA REPOSITORY:

==============================
{context}
==============================
"""

        self.telemetry.log(
            "🧑‍💻",
            "CODER",
            f"Sending verification task to "
            f"{self.coder.model}...",
        )

        result = self.coder.chat(
            system=CODER_SYSTEM,
            prompt=prompt,
        )

        self.telemetry.log(
            "🧩",
            "SYNTHESIS",
            "Building verified implementation plan...",
        )

        self.telemetry.log(
            "✅",
            "COMPLETE",
            "Coder verification finished.",
        )

        return result

    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        console.print(
            Panel(
                f"Project: {self.reader.root}\n"
                f"Reasoner: {self.reasoner.model}\n"
                f"Coder: {self.coder.model}\n\n"
                "Mode: READ-ONLY\n"
                f"Telemetry: "
                f"{self.telemetry.log_file}",
                title="🔥 AURA Local Dual Agent",
            )
        )

        # Reasoner
        audit = self.audit()

        console.print(
            Panel(
                Markdown(audit),
                title="🧠 Reasoner Audit",
            )
        )

        # Coder
        verify = self.verify_audit(
            audit
        )

        console.print(
            Panel(
                Markdown(verify),
                title="🧑‍💻 Coder Verification",
            )
        )

        # Final
        self.telemetry.log(
            "🏁",
            "SESSION",
            "Dual-model AURA audit completed.",
        )

        console.print(
            Panel(
                "Audit complete.\n\n"
                "No AURA files were modified.\n\n"
                f"Live telemetry:\n"
                f"{self.telemetry.log_file}",
                title="✅ COMPLETE",
            )
        )


# ============================================================
# ENTRY POINT
# ============================================================

def main():

    try:

        config_path = (
            Path(__file__).with_name(
                "config.yaml"
            )
        )

        agent = DualityLocalAgent(
            config_path=str(
                config_path
            )
        )

        agent.run()

    except KeyboardInterrupt:

        console.print(
            "\n[yellow]Interrupted.[/yellow]"
        )

        sys.exit(130)

    except Exception as exc:

        console.print(
            Panel(
                str(exc),
                title="❌ ERROR",
            )
        )

        sys.exit(1)


if __name__ == "__main__":
    main()