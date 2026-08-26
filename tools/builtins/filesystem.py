"""
Filesystem reading, and writing.

Confined to directories the owner explicitly listed, and to two separate
lists: `tools.allowed_paths` for reading, `tools.writable_paths` for
writing. With neither configured this module registers nothing at all.

The two lists are the point. A read grant is not a write grant - see the
comment above the write tools - and an owner who wants both says so
twice.

Containment is enforced by resolving the path and then checking that the
resolved result is inside a resolved root. Resolving first is what makes
`../../../.ssh/id_rsa`, a symlink out of the sandbox, and a Windows
short name all fail the same way. Checking the string before resolving
would catch none of them.
"""

import os
import tempfile
from pathlib import Path

from tools.base import Parameter, Tool, ToolRisk, fail, ok


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
# Writing
#
# Writes get their OWN root list, and that separation is the whole of the
# design. `allowed_paths` is a *reading* grant: an owner who listed their
# notes directory so Aura could read it did not thereby say she may
# overwrite it. Deriving write access from a read grant would hand the
# owner a capability they never asked for, which is the half of section 2
# that is easy to miss - the owner must be able to enable writing freely,
# and must not discover it already enabled. So `tools.writable_paths` is
# its own section, empty in the shipped config, and an owner who wants
# both lists the directory twice on purpose.
#
# Containment is the same `_contained` the readers use, not a second
# implementation of it. Resolving before checking is what makes
# `../../../.ssh/id_rsa`, a symlink pointing out of the sandbox, and a
# Windows short name all fail identically - and it matters more here than
# it does for reading. `resolve` follows a symlink that already exists at
# the target, so writing to `notes.txt` when `notes.txt` is a link to
# somewhere outside the root is refused rather than followed, and the
# owner's real file elsewhere is left alone.
#
# All four of these are DANGEROUS, which means gate 4 asks a human at the
# moment of the call. In server mode there is no human to ask and the
# call is refused - see `_build_tools` in `launcher/services.py`.
# ----------------------------------------------------------------------

MAX_WRITE_BYTES = 100_000


class WriteFileTool(Tool):
    """
    Create a text file, or replace one when told to in so many words.

    The `overwrite` parameter is not a safety rail against the owner -
    they can pass it on any call, and nothing about their configuration
    stops them. It is a requirement that the *model* state its intent.
    Replacing a file destroys what was there, section 21 says Aura "must
    not silently perform arbitrary high-impact actions", and a model that
    means to create `notes-2026.md` and mistypes it as `notes.md` would
    otherwise silently destroy a year of the owner's writing. Making the
    destructive reading of an ambiguous call the one the model has to ask
    for turns that mistake into a message naming both choices.
    """

    name = "write_file"
    description = "Create or replace a text file inside a writable directory"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(name="path", description="File to write"),
        Parameter(name="content", description="Text to write into it"),
        Parameter(
            name="overwrite",
            description="true to replace a file that already exists",
            required=False,
        ),
    )

    def __init__(self, roots: list[str] | None = None):

        self.roots = _resolve_roots(roots)

    def execute(self, path: str, content: str = "", overwrite=False) -> str:

        target = _contained(path, self.roots)

        data = _encoded(content)

        replacing = _flag(overwrite, "overwrite")

        if target.is_dir():
            raise ValueError(
                f"{target.name} is a directory, not a file"
            )

        existed = target.exists()

        if existed and not replacing:
            raise ValueError(
                f"{target.name} already exists. Pass overwrite=true to "
                f"replace it, or write to a different name."
            )

        # Refused rather than created. A missing parent is far more often
        # a mistyped path than a directory the caller meant to bring into
        # being - `nots/august.md` for `notes/august.md` - and creating it
        # would leave a stray tree behind and report success. This is also
        # what makes `create_directory` load bearing rather than
        # decorative: the caller says which of the two it meant.
        if not target.parent.is_dir():
            raise FileNotFoundError(
                f"the directory {target.parent.name} does not exist - "
                f"create it with create_directory first"
            )

        _atomic_write(target, data)

        verb = "replaced" if existed else "wrote"

        return f"{verb} {_shown(target, self.roots)} ({len(data)} bytes)"

    def verify(self, path: str, content: str = "", overwrite=False):
        """
        The postcondition of writing: the file reads back as what was sent.

        This is the one actuator in the PC layer whose postcondition is
        both readable and cheap, and the contrast with the other two is
        worth stating, because "no verify" and "a real verify" are both
        deliberate elsewhere. `focus_window` can read its own condition
        back but the answer decays under the foreground lock, so it polls.
        `run_command` cannot re-ask at all - the exit status *is* the
        postcondition, and asking again would mean running the command a
        second time for double the side effects. Here the file is still
        there, reading it costs one `read_bytes`, and a mismatch is a real
        failure rather than a race: a full disk, a quota, an antivirus
        rewriting the file as it lands, or a `content` that never arrived.

        Bytes, not text, and the write side is what makes that legal.
        `Path.write_text` would translate every `\\n` into `\\r\\n` on
        Windows, so a byte comparison against what the model sent would
        fail on any multi-line file - and worse, the file would not
        contain what was asked for. `_atomic_write` writes the encoded
        bytes untranslated, which is both the honest thing and the reason
        this comparison can be exact.
        """

        target = _contained(path, self.roots)

        if not target.is_file():
            return fail(f"{target.name} is not there after writing it")

        written = target.read_bytes()
        expected = _encoded(content)

        if written != expected:
            return fail(
                f"{target.name} does not contain what was written "
                f"({len(written)} bytes on disk, {len(expected)} sent)"
            )

        return ok(f"{target.name} reads back as written")


class AppendToFileTool(Tool):
    """
    Add to the end of a file without touching what is already in it.

    A separate tool rather than a mode on `write_file`, because the owner
    grants tools by name and these two carry different risks in kind, not
    in degree: appending cannot lose what was there, replacing can. An
    owner who wants Aura to keep a running log can allow this one and
    leave `write_file` unlisted, and Aura then has no way to blank the
    log she is keeping.
    """

    name = "append_to_file"
    description = "Add text to the end of a file inside a writable directory"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(name="path", description="File to append to"),
        Parameter(name="content", description="Text to add at the end"),
    )

    def __init__(self, roots: list[str] | None = None):

        self.roots = _resolve_roots(roots)

    def execute(self, path: str, content: str = "") -> str:

        target = _contained(path, self.roots)

        data = _encoded(content)

        if not data:
            raise ValueError("content is empty, so there is nothing to add")

        # The file must already exist. Shell `>>` would create it, and
        # that is the wrong default for a path a model chose: a mistyped
        # name silently becomes a new file that looks like a successful
        # append, and the owner's real log stays empty while Aura reports
        # writing to it every day. `write_file` is how a file starts.
        if not target.exists():
            raise FileNotFoundError(
                f"{target.name} does not exist - create it with write_file "
                f"first"
            )

        if not target.is_file():
            raise ValueError(f"{target.name} is a directory, not a file")

        before = target.stat().st_size

        if before + len(data) > MAX_WRITE_BYTES:
            raise ValueError(
                f"{target.name} would grow to "
                f"{before + len(data)} bytes, over the "
                f"{MAX_WRITE_BYTES} byte limit"
            )

        # Opened for append and closed, not read-modify-write. Reading the
        # whole file to add a line to it would lose anything another
        # program wrote in between, and the point of this tool is that it
        # cannot lose data.
        with open(target, "ab") as handle:
            handle.write(data)

        return f"added {len(data)} bytes to {_shown(target, self.roots)}"

    def verify(self, path: str, content: str = ""):
        """
        The postcondition: the file now ends with what was added.

        What this establishes and what it does not, said plainly because
        the gap is real. It proves the text arrived, at the end, intact -
        which is the failure worth catching, since a write that silently
        went nowhere is exactly what section 11 refuses to take on trust.
        It does **not** prove that nothing else about the file changed,
        because `verify` is handed the same arguments `execute` got and
        the length the file had beforehand is not among them. Stashing
        that length on the instance between the two calls would be a lie
        the moment two appends overlap, so it is not stashed, and this
        docstring says what the check is worth instead.
        """

        target = _contained(path, self.roots)

        if not target.is_file():
            return fail(f"{target.name} is not there after appending to it")

        data = _encoded(content)

        tail = target.read_bytes()[-len(data):] if data else b""

        if tail != data:
            return fail(
                f"{target.name} does not end with what was added"
            )

        return ok(f"{target.name} ends with the added text")


class CreateDirectoryTool(Tool):
    """
    Make a directory inside a writable root, parents and all.

    Parents are created here and refused by `write_file`, which is not an
    inconsistency. An extra empty directory is recoverable and costs the
    owner nothing; an extra *file* written down a mistyped path is a
    stray copy of real content that the owner will find months later and
    not know the provenance of. So the tool whose whole purpose is making
    a place to put things makes the whole path, and the tool that puts
    something there insists the place already exists.
    """

    name = "create_directory"
    description = "Create a directory inside a writable directory"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(name="path", description="Directory to create"),
    )

    def __init__(self, roots: list[str] | None = None):

        self.roots = _resolve_roots(roots)

    def execute(self, path: str) -> str:

        target = _contained(path, self.roots)

        if target.is_file():
            raise ValueError(
                f"{target.name} is already a file, not a directory"
            )

        # Idempotent, and honest about it in the output rather than
        # silently. "already there" and "made it" are different facts
        # about the machine, and a caller that gets "created" for a
        # directory full of the owner's files has been told something
        # untrue.
        if target.is_dir():
            return f"{_shown(target, self.roots)} already exists"

        target.mkdir(parents=True)

        return f"created {_shown(target, self.roots)}"

    def verify(self, path: str):
        """The postcondition: a directory is there now."""

        target = _contained(path, self.roots)

        if not target.is_dir():
            return fail(f"{target.name} is not a directory after creating it")

        return ok(f"{target.name} is a directory")


class DeleteFileTool(Tool):
    """
    Remove one file. Not a directory, and never a tree.

    Files only, and the restriction is not timidity. Section 24 asks for
    "filesystem operations", and recursive deletion is a different blast
    radius from every other operation in this module: `write_file` at the
    wrong path costs one file, and `rmtree` at the wrong path costs
    everything under it, with nothing to read back afterwards to find out
    what was lost. Nothing in section 24 asks for it, so it is not here,
    and a caller that wants a directory gone gets a message saying which
    tool removed which file instead of a silent surprise.

    There is no undo. The containment check, the DANGEROUS confirmation
    and the requirement that the file already exist are the safeguards,
    and the last of those is doing more work than it looks like: deleting
    something that was never there would otherwise report success and
    leave the caller believing a file it named is gone when the file it
    *meant* is still sitting there under a slightly different name.
    """

    name = "delete_file"
    description = "Delete one file inside a writable directory"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(name="path", description="File to delete"),
    )

    def __init__(self, roots: list[str] | None = None):

        self.roots = _resolve_roots(roots)

    def execute(self, path: str) -> str:

        target = _contained(path, self.roots)

        if target.is_dir():
            raise ValueError(
                f"{target.name} is a directory - delete_file removes one "
                f"file at a time and never a directory"
            )

        if not target.exists():
            raise FileNotFoundError(f"not found: {target.name}")

        size = target.stat().st_size

        target.unlink()

        return f"deleted {_shown(target, self.roots)} ({size} bytes)"

    def verify(self, path: str):
        """
        The postcondition of deleting: it is not there any more.

        Worth having even though `unlink` raises on failure, because on
        Windows it does not always: a file held open by another process
        can be unlinked into a pending state, and antivirus and backup
        software hold files open routinely. So the honest question is
        whether the path is gone, asked after the fact, rather than
        whether the call returned.
        """

        target = _contained(path, self.roots)

        if target.exists():
            return fail(f"{target.name} is still there after deleting it")

        return ok(f"{target.name} is gone")


# ----------------------------------------------------------------------
# Writing, mechanics
# ----------------------------------------------------------------------

def _shown(target: Path, roots: list[Path]) -> str:
    """
    How a path is named back to the caller: relative to its own root.

    `target.name` alone is what the reading tools say, and for them it is
    enough - a caller that asked to read one named file knows which one.
    The writing tools create structure, so `create_directory("a/b/c")`
    answering "created c" leaves out whether `a/b` was made as well, and
    `write_file("notes/august.md")` answering "wrote august.md" cannot be
    told apart from a write into the wrong folder.

    Relative to the root rather than absolute, because an absolute path
    names the owner's home directory and username, and this text goes
    into a prompt and so leaves the machine - the same reason
    `system_information` reports the disk but not the user. Forward
    slashes, so the same file reads the same way in a transcript written
    on either platform.

    The readers are deliberately left saying `target.name`. They work,
    they are tested, and changing a message with no defect behind it is
    how a working system acquires churn.
    """

    for root in roots:

        try:
            return target.relative_to(root).as_posix()
        except ValueError:
            continue

    return target.name


def _encoded(content) -> bytes:
    """
    The bytes a file should end up containing.

    Text only, and a dict is refused rather than serialised. A model
    writing a config file will happily send a mapping and expect JSON
    back, but there is no way to know from here whether it wanted two
    space indentation, sorted keys, or a trailing newline - and a file
    written in the wrong shape is a bug the owner debugs later, not an
    error anyone sees now. Refusing names the fix in one line.

    A number is refused for the same reason and no other: `0.1` reaching
    disk as `0.1` and as `0.10000000000000001` are both defensible, so
    the caller decides.
    """

    if content is None:
        return b""

    if not isinstance(content, str):
        raise ValueError(
            f"content must be text, not {type(content).__name__} - "
            f"format it yourself and send the result"
        )

    data = content.encode("utf-8")

    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(
            f"content is {len(data)} bytes, over the {MAX_WRITE_BYTES} "
            f"byte limit"
        )

    return data


def _flag(value, name: str) -> bool:
    """
    A boolean that survived JSON.

    The same vocabulary `core/settings_store.py::_boolean` accepts, on
    purpose, so the owner and the model meet one convention rather than
    two. Not imported from there: that function raises SettingsError, and
    pulling the settings store's error type into a tool would tie the two
    together for the sake of six words in a set.
    """

    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, str) and value.strip().lower() in {
        "true", "false", "1", "0", "yes", "no", "",
    }:
        return value.strip().lower() in {"true", "1", "yes"}

    raise ValueError(f"{name} must be true or false, not {value!r}")


def _atomic_write(target: Path, data: bytes) -> None:
    """
    Write `data` to `target` with no window where it is half written.

    A temporary file in the same directory, then `os.replace`, which is
    atomic within one filesystem. `core/settings_store.py`,
    `core/credentials.py` and `proactive/ledger.py` all do this and each
    says why: a half written file read at the next start is worse than no
    file at all. The reason is stronger here, because the file being
    replaced is not Aura's own bookkeeping - it is the owner's, and a
    crash or a full disk halfway through must leave them what they had
    rather than a truncated version of it.

    One deliberate difference from those three. They name the temporary
    `<path>.tmp`, which is safe for a fixed path Aura owns. Here the path
    came from a model, so `notes.tmp` may well be a file the owner
    already has, and writing it would destroy something nobody mentioned.
    `tempfile.mkstemp` in the same directory gives a name that cannot
    collide - and the same directory is also what keeps `os.replace`
    atomic, so the two requirements happen to agree.

    No newline translation. The bytes handed in are the bytes that land,
    which is what lets `verify` compare them exactly, and is also simply
    what the caller asked for.
    """

    handle, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=".aura-", suffix=".tmp"
    )

    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, target)

    except BaseException:
        # The replace never happened, so the original is intact and the
        # temporary is litter. Removing it is best effort: if it cannot be
        # removed the write has still failed correctly, and raising a
        # second error from the cleanup would hide the first one.
        try:
            os.unlink(temporary)
        except OSError:
            pass

        raise


# ----------------------------------------------------------------------
# Containment
# ----------------------------------------------------------------------

def _resolve_roots(roots: list[str] | None) -> list[Path]:

    resolved = []

    for root in roots or []:

        try:
            # pathlib on Windows can carry NUL/control characters through
            # ``resolve(strict=False)`` without asking the OS to validate
            # them. Reject such roots before resolution: an invalid root
            # must be dropped, never converted into a broad permission.
            raw = os.fspath(root)
            if not isinstance(raw, (str, bytes)):
                continue
            if isinstance(raw, bytes):
                invalid = any(byte < 32 for byte in raw)
            else:
                invalid = any(ord(character) < 32 for character in raw)
            if invalid:
                continue

            resolved.append(Path(raw).expanduser().resolve())
        except (OSError, RuntimeError, TypeError, ValueError):
            continue

    return resolved


def _contained(path: str, roots: list[Path]) -> Path:
    """
    Resolve `path` and prove it sits inside one of `roots`.

    Raises PermissionError otherwise. The executor turns that into a
    failed ToolResult, so a bad path is a denial rather than a crash.

    A relative path resolves against Aura's working directory, not
    against a root, and so almost always lands outside every one of them.
    That behaviour is deliberate and is left alone: with more than one
    root there is no non-arbitrary root to resolve `notes.md` against,
    and picking the first would silently write to a different directory
    than the caller pictured. What the relative case gets instead is its
    own message, because "outside the allowed directories" is a true but
    useless description of a bare filename, and a caller told only that
    will try another bare filename.

    Neither message repeats the resolved path back. It would name the
    owner's home directory and username, and this text goes into a prompt
    and so leaves the machine - the same reason `system_information`
    reports the disk but not the user. `target.name` is safe and is what
    the rest of this module says.
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

    if not Path(path).expanduser().is_absolute():
        raise PermissionError(
            f"{target.name!r} is a relative path, so it was looked for "
            f"beside Aura rather than in a permitted directory - give the "
            f"full path"
        )

    raise PermissionError("path is outside the allowed directories")
