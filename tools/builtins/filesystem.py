"""
Filesystem reading.

Read only, and confined to directories the user explicitly listed. With
no roots configured it reads nothing at all.

Containment is enforced by resolving the path and then checking that the
resolved result is inside a resolved root. Resolving first is what makes
`../../../.ssh/id_rsa`, a symlink out of the sandbox, and a Windows
short name all fail the same way. Checking the string before resolving
would catch none of them.
"""

from pathlib import Path

from tools.base import Parameter, Tool, ToolRisk


MAX_BYTES = 100_000
MAX_ENTRIES = 200


class ReadFileTool(Tool):

    name = "read_file"
    description = "Read a text file inside an allowed directory"
    risk = ToolRisk.SENSITIVE

    parameters = (
        Parameter(name="path", description="File to read"),
    )

    def __init__(self, roots: list[str] | None = None):

        self.roots = _resolve_roots(roots)

    def execute(self, path: str) -> str:

        target = _contained(path, self.roots)

        if not target.exists():
            raise FileNotFoundError(f"not found: {target.name}")

        if not target.is_file():
            raise ValueError(f"not a file: {target.name}")

        data = target.read_bytes()[:MAX_BYTES]

        text = data.decode("utf-8", errors="replace")

        if target.stat().st_size > MAX_BYTES:
            text += "\n...(truncated)"

        return text


class ListDirectoryTool(Tool):

    name = "list_directory"
    description = "List the contents of an allowed directory"
    risk = ToolRisk.SENSITIVE

    parameters = (
        Parameter(name="path", description="Directory to list"),
    )

    def __init__(self, roots: list[str] | None = None):

        self.roots = _resolve_roots(roots)

    def execute(self, path: str) -> str:

        target = _contained(path, self.roots)

        if not target.is_dir():
            raise ValueError(f"not a directory: {target.name}")

        entries = []

        for entry in sorted(target.iterdir())[:MAX_ENTRIES]:

            marker = "/" if entry.is_dir() else ""

            entries.append(f"{entry.name}{marker}")

        if not entries:
            return "(empty)"

        return "\n".join(entries)


# ----------------------------------------------------------------------
# Containment
# ----------------------------------------------------------------------

def _resolve_roots(roots: list[str] | None) -> list[Path]:

    resolved = []

    for root in roots or []:

        try:
            resolved.append(Path(root).expanduser().resolve())
        except Exception:
            continue

    return resolved


def _contained(path: str, roots: list[Path]) -> Path:
    """
    Resolve `path` and prove it sits inside one of `roots`.

    Raises PermissionError otherwise. The executor turns that into a
    failed ToolResult, so a bad path is a denial rather than a crash.
    """

    if not roots:
        raise PermissionError("no directories are allowed")

    if not str(path).strip():
        raise ValueError("path is required")

    try:
        target = Path(path).expanduser().resolve()
    except Exception as error:
        raise ValueError(f"invalid path: {error}") from error

    for root in roots:

        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue

    raise PermissionError("path is outside the allowed directories")
