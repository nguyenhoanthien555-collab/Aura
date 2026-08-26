"""
Filesystem writing (phase 18.3, section 24).

Four DANGEROUS tools that change the owner's disk, so most of what is
tested here is what they refuse and what they leave alone. Three
properties carry the phase, and each is asserted against real files
rather than a mock, because a mock would only assert what I already
believed about the filesystem:

  * a reading grant is not a writing grant - `tools.allowed_paths` does
    not make anything writable, and the four tools are not registered at
    all until `tools.writable_paths` says so;
  * nothing lands outside a writable root, by traversal, by symlink, or
    by absolute path, and the file outside is byte-identical afterwards;
  * a write that fails leaves what the owner had, not a truncated
    version of it.

The `verify()` on all four is the point of comparison for the rest of the
PC layer: this is the one place where the postcondition is both readable
and cheap, so the tests read the file back and also check that the
executor downgrades a success `verify` denies.
"""

import io
import os
from pathlib import Path

import pytest
import yaml

from tools.base import ToolProtocol, ToolRisk
from tools.builtins.filesystem import (
    MAX_WRITE_BYTES,
    AppendToFileTool,
    CreateDirectoryTool,
    DeleteFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
    _atomic_write,
    _encoded,
    _flag,
    _shown,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry
from tools.registry import ToolRegistry


NL = chr(10)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def writable(tmp_path):
    """
    A writable root, with the owner's untouchable file just outside it.

    `secret.txt` is a sibling of the root rather than a distant path on
    purpose: `..` from inside the root reaches it in one step, which is
    the traversal a model would actually produce.
    """

    root = tmp_path / "writable"
    root.mkdir()

    (tmp_path / "secret.txt").write_text("the owner's own file", encoding="utf-8")

    return root


@pytest.fixture
def tools(writable):
    """All four writers over one root, as the factory builds them."""

    roots = [str(writable)]

    return {
        "write": WriteFileTool(roots),
        "append": AppendToFileTool(roots),
        "mkdir": CreateDirectoryTool(roots),
        "delete": DeleteFileTool(roots),
    }


def executor_for(*instances, allowed=None, approve=True):
    """An executor with these tools registered and DANGEROUS approved."""

    registry = ToolRegistry()

    for tool in instances:
        registry.register(tool)

    names = allowed if allowed is not None else [t.name for t in instances]

    return ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=names,
            auto_approve=["safe", "sensitive", "dangerous"] if approve else ["safe"],
        ),
        confirm=(lambda tool, arguments: True) if approve else None,
    )


# ----------------------------------------------------------------------
# Section 2: a reading grant is not a writing grant
# ----------------------------------------------------------------------

class TestReadingIsNotWriting:
    """
    The central decision of the phase, tested from both ends.

    An owner adds a folder to `allowed_paths` so Aura can look something
    up. If writing inherited those roots, that one act would also have
    granted permission to overwrite everything in it, and the owner would
    never have been asked. Section 2: the owner must be able to enable
    writing freely, and must not discover it already enabled.
    """

    def test_allowed_paths_alone_registers_no_writer(self, writable):

        registry = build_registry({"allowed_paths": [str(writable)]})

        assert registry.has("read_file")
        assert registry.has("list_directory")

        for name in (
            "write_file", "append_to_file", "create_directory", "delete_file"
        ):
            assert not registry.has(name), f"{name} came from a read grant"

    def test_writable_paths_registers_all_four(self, writable):

        registry = build_registry({"writable_paths": [str(writable)]})

        for name in (
            "write_file", "append_to_file", "create_directory", "delete_file"
        ):
            assert registry.has(name)

    def test_writable_paths_alone_registers_no_reader(self, writable):
        """
        And the converse, which matters just as much: granting writes
        does not quietly grant reads. `verify` reads the file back, but
        that is the tool checking its own postcondition, not the owner
        having allowed `read_file` on the folder.
        """

        registry = build_registry({"writable_paths": [str(writable)]})

        assert not registry.has("read_file")
        assert not registry.has("list_directory")

    def test_the_two_lists_are_independent(self, tmp_path):

        readable = tmp_path / "readable"
        writeable = tmp_path / "writeable"
        readable.mkdir()
        writeable.mkdir()
        (readable / "notes.txt").write_text("read me", encoding="utf-8")

        registry = build_registry({
            "allowed_paths": [str(readable)],
            "writable_paths": [str(writeable)],
        })

        writer = registry.get("write_file")
        reader = registry.get("read_file")

        # The reader's root is not writable.
        with pytest.raises(PermissionError):
            writer.execute(str(readable / "notes.txt"), "clobber", overwrite=True)

        assert (readable / "notes.txt").read_text(encoding="utf-8") == "read me"

        # The writer's root is not readable.
        (writeable / "made.txt").write_text("written", encoding="utf-8")

        with pytest.raises(PermissionError):
            reader.execute(str(writeable / "made.txt"))

    def test_no_writable_root_means_nothing_is_writable(self, writable):
        """
        The tool can be constructed with no roots - the factory will not
        do it, but a plugin or a test might - and it must then write
        nothing rather than default to somewhere.
        """

        tool = WriteFileTool([])

        with pytest.raises(PermissionError):
            tool.execute(str(writable / "notes.md"), "hello")

        assert not (writable / "notes.md").exists()

    def test_an_unresolvable_root_is_dropped_not_treated_as_everything(self):
        """
        `_resolve_roots` skips a root it cannot resolve. The failure mode
        worth excluding is a dropped root leaving an empty list that some
        later code reads as "no restriction".
        """

        tool = WriteFileTool(["\0not a path"])

        assert tool.roots == []

        with pytest.raises(PermissionError):
            tool.execute("anywhere.txt", "hello")


# ----------------------------------------------------------------------
# Containment
# ----------------------------------------------------------------------

class TestNothingLandsOutsideTheRoot:
    """
    Every escape attempt, and in each case the file outside is checked
    afterwards. A refusal that still modified something would pass a test
    that only looked at the exception.
    """

    def test_an_absolute_path_outside_is_refused(self, tools, writable):

        outside = writable.parent / "secret.txt"
        before = outside.read_bytes()

        with pytest.raises(PermissionError):
            tools["write"].execute(str(outside), "clobber", overwrite=True)

        assert outside.read_bytes() == before

    def test_dot_dot_traversal_is_refused(self, tools, writable):

        outside = writable.parent / "secret.txt"
        before = outside.read_bytes()

        with pytest.raises(PermissionError):
            tools["write"].execute(
                str(writable / ".." / "secret.txt"), "clobber", overwrite=True
            )

        assert outside.read_bytes() == before

    def test_a_symlink_pointing_out_is_refused(self, tools, writable):
        """
        Resolving before checking is what catches this, and it matters
        more for writing than for reading: a link at the target would
        otherwise be followed and the owner's real file elsewhere
        replaced, with the link left looking untouched.
        """

        outside = writable.parent / "secret.txt"
        link = writable / "innocent.txt"

        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlinks unavailable here: {error}")

        before = outside.read_bytes()

        with pytest.raises(PermissionError):
            tools["write"].execute(str(link), "clobber", overwrite=True)

        assert outside.read_bytes() == before

    def test_a_junction_pointing_out_is_refused(self, tools, writable):
        """
        The same escape as the symlink test, by the route that actually
        works on this host: creating a symlink on Windows needs a
        privilege the test runner does not hold, but a directory junction
        needs none, and `resolve()` sees through both. Without this the
        most important property in the module would be permanently
        skipped here and only exercised on CI.

        The junction is removed inside the test rather than left to
        `tmp_path` cleanup, because `shutil.rmtree` follows a junction
        into its target.
        """

        import subprocess

        private = writable.parent / "private"
        private.mkdir()
        (private / "secret.txt").write_bytes(b"the owner's own file")

        link = writable / "shortcut"

        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(private)],
            capture_output=True, text=True,
        )

        if made.returncode != 0:
            pytest.skip(f"junctions unavailable here: {made.stderr.strip()}")

        try:
            with pytest.raises(PermissionError):
                tools["write"].execute(
                    str(link / "secret.txt"), "clobber", overwrite=True
                )

            with pytest.raises(PermissionError):
                tools["delete"].execute(str(link / "secret.txt"))

            assert (private / "secret.txt").read_bytes() == b"the owner's own file"
        finally:
            os.rmdir(link)

    def test_a_traversal_delete_is_refused(self, tools, writable):

        outside = writable.parent / "secret.txt"

        with pytest.raises(PermissionError):
            tools["delete"].execute(str(writable / ".." / "secret.txt"))

        assert outside.exists()

    def test_a_traversal_mkdir_is_refused(self, tools, writable):

        with pytest.raises(PermissionError):
            tools["mkdir"].execute(str(writable / ".." / "made"))

        assert not (writable.parent / "made").exists()

    def test_a_traversal_append_is_refused(self, tools, writable):

        outside = writable.parent / "secret.txt"
        before = outside.read_bytes()

        with pytest.raises(PermissionError):
            tools["append"].execute(str(writable / ".." / "secret.txt"), "more")

        assert outside.read_bytes() == before

    def test_a_relative_path_says_it_is_relative(self, tools):
        """
        A bare filename resolves against Aura's working directory, so it
        is outside every root - true, and useless to a caller who will
        just try another bare filename. The message has to name the
        actual problem.
        """

        with pytest.raises(PermissionError) as raised:
            tools["write"].execute("notes.md", "hello")

        message = str(raised.value).lower()

        assert "relative" in message
        assert "full path" in message

    def test_an_empty_path_is_refused(self, tools):

        for value in ("", "   "):
            with pytest.raises(ValueError):
                tools["write"].execute(value, "hello")

    def test_a_deeper_path_inside_the_root_is_allowed(self, tools, writable):
        """Containment is a boundary, not a depth limit."""

        (writable / "a" / "b").mkdir(parents=True)

        tools["write"].execute(str(writable / "a" / "b" / "deep.md"), "deep")

        assert (writable / "a" / "b" / "deep.md").read_text() == "deep"


# ----------------------------------------------------------------------
# Replacing is asked for, never assumed
# ----------------------------------------------------------------------

class TestReplacingIsAskedFor:
    """
    Section 21: Aura "must not silently perform arbitrary high-impact
    actions". Destroying a file the owner wrote is high-impact, and a
    model that means `notes-2026.md` and types `notes.md` would otherwise
    do it silently.
    """

    def test_a_new_file_needs_no_flag(self, tools, writable):

        result = tools["write"].execute(str(writable / "new.md"), "fresh")

        assert "wrote" in result
        assert (writable / "new.md").read_text() == "fresh"

    def test_an_existing_file_is_not_replaced_without_the_flag(
        self, tools, writable
    ):

        target = writable / "notes.md"
        target.write_text("a year of writing", encoding="utf-8")

        with pytest.raises(ValueError) as raised:
            tools["write"].execute(str(target), "clobber")

        assert target.read_text(encoding="utf-8") == "a year of writing"

        message = str(raised.value)

        # The message has to name both ways out, or the model retries the
        # same call and gets the same refusal.
        assert "overwrite=true" in message
        assert "different name" in message

    def test_the_flag_replaces(self, tools, writable):

        target = writable / "notes.md"
        target.write_text("old", encoding="utf-8")

        result = tools["write"].execute(str(target), "new", overwrite=True)

        assert "replaced" in result
        assert target.read_text(encoding="utf-8") == "new"

    def test_replaced_and_wrote_are_different_words(self, tools, writable):
        """
        Creating and destroying are different facts about the machine, so
        the output distinguishes them even when both succeeded.
        """

        target = writable / "notes.md"

        first = tools["write"].execute(str(target), "one")
        second = tools["write"].execute(str(target), "two", overwrite=True)

        assert "wrote" in first and "replaced" not in first
        assert "replaced" in second

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True), (False, False),
            ("true", True), ("True", True), ("TRUE", True),
            ("yes", True), ("1", True),
            ("false", False), ("no", False), ("0", False),
            ("", False), (None, False),
            ("  true  ", True),
        ],
    )
    def test_the_flag_survives_json(self, value, expected):
        """
        A model sends `"true"` as often as `true`. The vocabulary is the
        one `core/settings_store.py::_boolean` already accepts, so the
        owner and the model meet one convention.
        """

        assert _flag(value, "overwrite") is expected

    @pytest.mark.parametrize("value", ["maybe", "on", 1, 0, [], {}, 1.5])
    def test_an_unrecognised_flag_is_refused_not_guessed(self, value):
        """
        `1` and `on` are refused deliberately. Guessing "probably yes"
        for a flag whose whole purpose is to require a stated intent
        would defeat the flag.
        """

        with pytest.raises(ValueError) as raised:
            _flag(value, "overwrite")

        assert "overwrite" in str(raised.value)

    def test_a_bad_flag_writes_nothing(self, tools, writable):

        with pytest.raises(ValueError):
            tools["write"].execute(str(writable / "n.md"), "x", overwrite="maybe")

        assert not (writable / "n.md").exists()

    def test_a_directory_is_never_replaced_by_a_file(self, tools, writable):

        (writable / "folder").mkdir()

        with pytest.raises(ValueError) as raised:
            tools["write"].execute(str(writable / "folder"), "x", overwrite=True)

        assert "directory" in str(raised.value)
        assert (writable / "folder").is_dir()


# ----------------------------------------------------------------------
# What was sent is what lands
# ----------------------------------------------------------------------

class TestWhatWasSentIsWhatLands:

    def test_newlines_are_not_translated(self, tools, writable):
        """
        `Path.write_text` turns every `\\n` into `\\r\\n` on Windows, so a
        two-line file would not contain what was asked for and a byte
        comparison in `verify` could never pass. `_atomic_write` writes
        the encoded bytes untranslated.
        """

        body = "one" + NL + "two" + NL

        tools["write"].execute(str(writable / "n.md"), body)

        assert (writable / "n.md").read_bytes() == body.encode("utf-8")
        assert b"\r\n" not in (writable / "n.md").read_bytes()

    def test_carriage_returns_the_caller_sent_are_kept(self, tools, writable):
        """The converse: no translation means none in either direction."""

        body = "one\r\ntwo\r\n"

        tools["write"].execute(str(writable / "crlf.md"), body)

        assert (writable / "crlf.md").read_bytes() == body.encode("utf-8")

    def test_unicode_survives(self, tools, writable):

        body = "tên: Thiên" + NL + "名前" + NL + "🌤"

        tools["write"].execute(str(writable / "u.md"), body)

        assert (writable / "u.md").read_text(encoding="utf-8") == body

    def test_empty_content_makes_an_empty_file(self, tools, writable):
        """
        An empty file is a real thing to want, and distinguishable from
        no file at all - so this succeeds rather than being refused as
        pointless.
        """

        tools["write"].execute(str(writable / "empty.md"), "")

        assert (writable / "empty.md").exists()
        assert (writable / "empty.md").read_bytes() == b""

    def test_the_byte_count_reported_is_encoded_bytes_not_characters(
        self, tools, writable
    ):
        """
        `"名前"` is two characters and six bytes. The number in the output
        is what the disk holds, because that is the number the owner can
        check.
        """

        result = tools["write"].execute(str(writable / "u.md"), "名前")

        assert "6 bytes" in result

    @pytest.mark.parametrize(
        "value", [{"a": 1}, [1, 2], 42, 1.5, True]
    )
    def test_content_that_is_not_text_is_refused(self, value):
        """
        A model writing a config file will send a mapping and expect JSON
        back. There is no way to know from here whether it wanted two
        space indentation or sorted keys, and a file in the wrong shape
        is a bug found much later, so the refusal names the fix.
        """

        with pytest.raises(ValueError) as raised:
            _encoded(value)

        assert "must be text" in str(raised.value)

    def test_missing_content_is_an_empty_file_not_a_crash(self):

        assert _encoded(None) == b""

    def test_content_over_the_limit_is_refused_not_truncated(self, tools, writable):
        """
        Truncating would be silent data loss and would then pass `verify`
        only if `verify` truncated identically - two wrongs agreeing. The
        message names the size and the limit so the caller can split.
        """

        with pytest.raises(ValueError) as raised:
            tools["write"].execute(
                str(writable / "big.md"), "x" * (MAX_WRITE_BYTES + 1)
            )

        message = str(raised.value)

        assert str(MAX_WRITE_BYTES) in message
        assert not (writable / "big.md").exists()

    def test_exactly_the_limit_is_allowed(self, tools, writable):
        """An off-by-one here would be a silent capability cut."""

        tools["write"].execute(str(writable / "max.md"), "x" * MAX_WRITE_BYTES)

        assert (writable / "max.md").stat().st_size == MAX_WRITE_BYTES


# ----------------------------------------------------------------------
# Section 11: the postcondition is read back
# ----------------------------------------------------------------------

class TestTheFileIsReadBack:
    """
    This is the one actuator in the PC layer whose postcondition is both
    readable and cheap, and the contrast is the point. `focus_window` can
    read its condition back but the answer decays, so it polls;
    `run_command` cannot re-ask at all, because the exit status *is* the
    postcondition and asking again means running the command twice.
    """

    def test_verify_confirms_a_real_write(self, tools, writable):

        path = str(writable / "n.md")

        tools["write"].execute(path, "hello")

        assert tools["write"].verify(path, "hello").ok

    def test_verify_catches_a_file_that_does_not_match(self, tools, writable):
        """
        Something between the call and the disk changed the file: a full
        disk, a quota, antivirus rewriting it as it lands. `verify` is
        what turns that into a reported failure instead of a success.
        """

        path = writable / "n.md"

        tools["write"].execute(str(path), "hello")

        path.write_bytes(b"something else")

        verdict = tools["write"].verify(str(path), "hello")

        assert not verdict.ok
        assert "does not contain" in verdict.error

    def test_verify_catches_a_file_that_vanished(self, tools, writable):

        path = writable / "n.md"

        tools["write"].execute(str(path), "hello")
        path.unlink()

        verdict = tools["write"].verify(str(path), "hello")

        assert not verdict.ok
        assert "not there" in verdict.error

    def test_the_executor_downgrades_a_write_verify_denies(
        self, tools, writable, monkeypatch
    ):
        """
        End to end, because a `verify` the framework never calls is
        decoration. The write is made to land wrong by patching the
        atomic write to put different bytes on disk.
        """

        def wrong_bytes(target, data):
            Path(target).write_bytes(b"not what was sent")

        monkeypatch.setattr(
            "tools.builtins.filesystem._atomic_write", wrong_bytes
        )

        executor = executor_for(tools["write"])

        result = executor.execute(
            "write_file", {"path": str(writable / "n.md"), "content": "hello"}
        )

        assert not result.ok
        assert "does not contain" in result.error

    def test_a_verify_that_cannot_read_fails_closed(
        self, tools, writable, monkeypatch
    ):
        """
        An unreadable postcondition is unverified, not confirmed - the
        executor's rule, checked here through a real tool.
        """

        executor = executor_for(tools["write"])

        original = WriteFileTool.verify

        def exploding(self, *args, **kwargs):
            raise OSError("disk went away")

        monkeypatch.setattr(WriteFileTool, "verify", exploding)

        result = executor.execute(
            "write_file", {"path": str(writable / "n.md"), "content": "hello"}
        )

        assert not result.ok
        assert "could not be verified" in result.error

        monkeypatch.setattr(WriteFileTool, "verify", original)

    def test_every_writer_offers_a_verify(self, tools):
        """
        All four postconditions are readable, so all four have one. The
        absence of `verify` elsewhere means "execute already told the
        whole truth", and it would be the wrong claim here.
        """

        for tool in tools.values():
            assert callable(getattr(tool, "verify", None)), tool.name

    def test_verify_takes_the_same_arguments_as_execute(self, tools):
        """
        The executor calls `verify(**arguments)` with exactly what
        `execute` got. A signature that drifts raises TypeError and the
        result is reported unverified - a failure that looks like a disk
        problem.
        """

        import inspect

        for tool in tools.values():

            execute = inspect.signature(tool.execute).parameters
            verify = inspect.signature(tool.verify).parameters

            assert set(execute) == set(verify), tool.name


# ----------------------------------------------------------------------
# Appending cannot lose what was there
# ----------------------------------------------------------------------

class TestAppendingCannotLose:

    def test_appending_keeps_what_was_there(self, tools, writable):

        path = writable / "log.md"
        path.write_bytes(b"first" + NL.encode())

        tools["append"].execute(str(path), "second" + NL)

        assert path.read_bytes() == b"first" + NL.encode() + b"second" + NL.encode()

    def test_appending_to_a_missing_file_is_refused(self, tools, writable):
        """
        Shell `>>` would create it, and that is the wrong default for a
        path a model chose: a mistyped name becomes a new file that looks
        like a successful append, and the owner's real log stays empty
        while Aura reports writing to it every day.
        """

        with pytest.raises(FileNotFoundError) as raised:
            tools["append"].execute(str(writable / "ghost.md"), "line")

        assert "write_file" in str(raised.value)
        assert not (writable / "ghost.md").exists()

    def test_appending_nothing_is_refused(self, tools, writable):

        path = writable / "log.md"
        path.write_bytes(b"first")

        with pytest.raises(ValueError):
            tools["append"].execute(str(path), "")

        assert path.read_bytes() == b"first"

    def test_appending_to_a_directory_is_refused(self, tools, writable):

        (writable / "folder").mkdir()

        with pytest.raises(ValueError):
            tools["append"].execute(str(writable / "folder"), "x")

    def test_the_total_size_is_bounded_not_just_the_addition(
        self, tools, writable
    ):
        """
        Otherwise a bounded append repeated is an unbounded file, and the
        limit is a formality.
        """

        path = writable / "log.md"
        path.write_bytes(b"x" * (MAX_WRITE_BYTES - 5))

        with pytest.raises(ValueError) as raised:
            tools["append"].execute(str(path), "x" * 10)

        assert str(MAX_WRITE_BYTES) in str(raised.value)
        assert path.stat().st_size == MAX_WRITE_BYTES - 5

    def test_verify_checks_the_tail(self, tools, writable):

        path = writable / "log.md"
        path.write_bytes(b"first")

        tools["append"].execute(str(path), "second")

        assert tools["append"].verify(str(path), "second").ok

    def test_verify_notices_the_tail_is_wrong(self, tools, writable):

        path = writable / "log.md"
        path.write_bytes(b"first")

        tools["append"].execute(str(path), "second")

        path.write_bytes(b"first" + b"third")

        verdict = tools["append"].verify(str(path), "second")

        assert not verdict.ok
        assert "does not end with" in verdict.error

    def test_appending_unicode_is_measured_in_bytes(self, tools, writable):
        """
        The tail comparison slices bytes, so a multi-byte character at the
        boundary would corrupt the check if it sliced characters.
        """

        path = writable / "log.md"
        path.write_bytes("tên".encode("utf-8"))

        tools["append"].execute(str(path), "名前")

        assert tools["append"].verify(str(path), "名前").ok
        assert path.read_text(encoding="utf-8") == "tên名前"


# ----------------------------------------------------------------------
# create_directory
# ----------------------------------------------------------------------

class TestCreatingDirectories:

    def test_the_whole_path_is_made(self, tools, writable):
        """
        Parents are created here and refused by `write_file`, which is not
        an inconsistency: an extra empty directory costs nothing, an extra
        *file* down a mistyped path is a stray copy of real content.
        """

        tools["mkdir"].execute(str(writable / "a" / "b" / "c"))

        assert (writable / "a" / "b" / "c").is_dir()

    def test_the_path_is_named_back_relative_to_the_root(self, tools, writable):
        """
        `target.name` alone would answer "created c" for `a/b/c`, which
        does not say whether `a/b` was made too.
        """

        result = tools["mkdir"].execute(str(writable / "a" / "b" / "c"))

        assert "a/b/c" in result

    def test_making_one_that_exists_says_so(self, tools, writable):
        """
        Idempotent, and honest in the output rather than silently. A
        caller told "created" about a directory full of the owner's files
        has been told something untrue.
        """

        (writable / "folder").mkdir()

        result = tools["mkdir"].execute(str(writable / "folder"))

        assert "already exists" in result
        assert "created" not in result

    def test_a_file_in_the_way_is_refused(self, tools, writable):

        (writable / "thing").write_text("a file", encoding="utf-8")

        with pytest.raises(ValueError):
            tools["mkdir"].execute(str(writable / "thing"))

        assert (writable / "thing").read_text() == "a file"

    def test_verify_confirms_it(self, tools, writable):

        path = str(writable / "a" / "b")

        tools["mkdir"].execute(path)

        assert tools["mkdir"].verify(path).ok

    def test_verify_notices_it_is_missing(self, tools, writable):

        path = writable / "gone"

        tools["mkdir"].execute(str(path))
        path.rmdir()

        assert not tools["mkdir"].verify(str(path)).ok


# ----------------------------------------------------------------------
# delete_file
# ----------------------------------------------------------------------

class TestDeleting:

    def test_one_file_goes(self, tools, writable):

        path = writable / "gone.md"
        path.write_text("bye", encoding="utf-8")

        result = tools["delete"].execute(str(path))

        assert not path.exists()
        assert "3 bytes" in result

    def test_a_directory_is_never_deleted(self, tools, writable):
        """
        Section 24 asks for "filesystem operations"; recursive deletion is
        a different blast radius from everything else in the module, with
        nothing to read back afterwards to learn what was lost. It is not
        here, and the refusal says so rather than failing obscurely.
        """

        folder = writable / "folder"
        folder.mkdir()
        (folder / "inside.md").write_text("still here", encoding="utf-8")

        with pytest.raises(ValueError) as raised:
            tools["delete"].execute(str(folder))

        assert "never a directory" in str(raised.value)
        assert (folder / "inside.md").read_text() == "still here"

    def test_deleting_something_absent_is_an_error_not_a_success(
        self, tools, writable
    ):
        """
        Reporting success would leave the caller believing a file it named
        is gone, when the file it *meant* is still there under a slightly
        different name.
        """

        with pytest.raises(FileNotFoundError):
            tools["delete"].execute(str(writable / "ghost.md"))

    def test_verify_confirms_absence(self, tools, writable):

        path = writable / "gone.md"
        path.write_text("bye", encoding="utf-8")

        tools["delete"].execute(str(path))

        assert tools["delete"].verify(str(path)).ok

    def test_verify_notices_the_file_is_still_there(self, tools, writable):
        """
        On Windows `unlink` does not always mean gone: a file held open by
        another process - antivirus and backup software hold files open
        routinely - can be unlinked into a pending state. So the question
        asked afterwards is whether the path is gone, not whether the call
        returned.
        """

        path = writable / "gone.md"
        path.write_text("bye", encoding="utf-8")

        tools["delete"].execute(str(path))

        path.write_text("back", encoding="utf-8")

        verdict = tools["delete"].verify(str(path))

        assert not verdict.ok
        assert "still there" in verdict.error


# ----------------------------------------------------------------------
# A failed write leaves what the owner had
# ----------------------------------------------------------------------

class TestAFailedWriteLosesNothing:
    """
    The reason `_atomic_write` exists. `settings_store`, `credentials` and
    `proactive/ledger` all write this way because a half-written file read
    at the next start is worse than no file; here the file is the
    *owner's*, so the requirement is stronger.
    """

    def test_a_failure_at_the_replace_leaves_the_original(
        self, tools, writable, monkeypatch
    ):

        path = writable / "notes.md"
        path.write_bytes(b"a year of writing")

        def refuse(source, target):
            raise OSError("simulated failure at the last step")

        monkeypatch.setattr("tools.builtins.filesystem.os.replace", refuse)

        with pytest.raises(OSError):
            tools["write"].execute(str(path), "new content", overwrite=True)

        assert path.read_bytes() == b"a year of writing"

    def test_a_failed_write_leaves_no_temporary_behind(
        self, tools, writable, monkeypatch
    ):
        """
        The temporary is litter in the owner's own folder if it survives,
        and it would show up in their file manager next to their work.
        """

        path = writable / "notes.md"
        path.write_bytes(b"original")

        def refuse(source, target):
            raise OSError("simulated failure")

        monkeypatch.setattr("tools.builtins.filesystem.os.replace", refuse)

        with pytest.raises(OSError):
            tools["write"].execute(str(path), "new", overwrite=True)

        assert sorted(p.name for p in writable.iterdir()) == ["notes.md"]

    def test_a_cleanup_failure_does_not_hide_the_real_error(
        self, tools, writable, monkeypatch
    ):
        """
        If the temporary cannot be removed the write has still failed
        correctly, and raising from the cleanup would replace the real
        reason with a confusing one.
        """

        path = writable / "notes.md"
        path.write_bytes(b"original")

        monkeypatch.setattr(
            "tools.builtins.filesystem.os.replace",
            lambda source, target: (_ for _ in ()).throw(OSError("the real reason")),
        )
        monkeypatch.setattr(
            "tools.builtins.filesystem.os.unlink",
            lambda p: (_ for _ in ()).throw(OSError("cleanup also failed")),
        )

        with pytest.raises(OSError) as raised:
            tools["write"].execute(str(path), "new", overwrite=True)

        assert "the real reason" in str(raised.value)

    def test_a_successful_write_leaves_no_temporary_behind(
        self, tools, writable
    ):

        tools["write"].execute(str(writable / "notes.md"), "content")

        assert sorted(p.name for p in writable.iterdir()) == ["notes.md"]

    def test_the_temporary_cannot_collide_with_the_owners_own_file(
        self, writable
    ):
        """
        `settings_store` names its temporary `<path>.tmp`, which is safe
        for a fixed path Aura owns. Here the path comes from a model, so
        `notes.tmp` may be a file the owner already has - `mkstemp` in the
        same directory gives a name that cannot collide, and the same
        directory is what keeps `os.replace` atomic.
        """

        # Both plausible spellings of the naive name. `settings_store`
        # uses `with_suffix(suffix + ".tmp")`, which for `notes.md` gives
        # `notes.md.tmp`; the shorter `notes.tmp` is the other obvious
        # guess. A test that named only one of them would pass against an
        # implementation that clobbered the other.
        decoys = {
            writable / "notes.md.tmp": b"the owner's own scratch file",
            writable / "notes.tmp": b"and another one",
        }

        for path, content in decoys.items():
            path.write_bytes(content)

        WriteFileTool([str(writable)]).execute(str(writable / "notes.md"), "new")

        for path, content in decoys.items():
            assert path.is_file(), f"{path.name} was renamed away"
            assert path.read_bytes() == content, f"{path.name} was overwritten"

    def test_a_folder_on_another_volume_can_still_be_written(self):
        """
        The temporary is created in the target's own directory, and that
        is load-bearing rather than tidy: `os.replace` across volumes
        fails on Windows with ERROR_NOT_SAME_DEVICE, because CPython does
        not pass MOVEFILE_COPY_ALLOWED. Measured on this host - `%TEMP%`
        is on C: and the repository is on D: - so a temporary in the
        system temp directory would make every write to a folder on any
        other drive fail, which for a typical owner is most of the folders
        they would grant.

        `tmp_path` cannot catch this, because pytest puts it under
        `%TEMP%`, so the same-volume case always holds there. This test
        goes looking for a second volume and skips when there is not one.
        """

        import tempfile

        system = Path(tempfile.gettempdir()).resolve().drive
        other = Path.cwd().resolve().drive

        if not other or other == system:
            pytest.skip(
                f"no second volume here (temp and cwd are both on {system})"
            )

        try:
            elsewhere = Path(tempfile.mkdtemp(prefix="aura-test-", dir=other + os.sep))
        except OSError as error:
            pytest.skip(f"{other} root is not writable: {error}")

        try:
            body = "written across volumes" + NL

            tool = WriteFileTool([str(elsewhere)])
            tool.execute(str(elsewhere / "notes.md"), body)

            assert (elsewhere / "notes.md").read_bytes() == body.encode("utf-8")
            assert tool.verify(str(elsewhere / "notes.md"), body).ok

            # And no temporary was left on either volume.
            assert sorted(p.name for p in elsewhere.iterdir()) == ["notes.md"]
        finally:
            import shutil
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_the_temporary_is_made_in_the_targets_own_directory(self):
        """
        The always-runs half of the test above, which needs a second
        volume to exist. Structural, because the property is about where
        `mkstemp` is pointed and there is no way to observe that from a
        successful write on one volume.
        """

        source = io.open(
            "tools/builtins/filesystem.py", encoding="utf-8"
        ).read()

        body = source.split("def _atomic_write")[1]

        assert "dir=str(target.parent)" in body

    def test_the_bytes_reach_the_disk_not_just_the_buffer(self, writable):
        """
        `_atomic_write` fsyncs before replacing. Without it the rename can
        be durable while the contents are not, which on a power loss is a
        file that exists and is empty - the exact outcome the temporary
        was there to prevent.
        """

        source = io.open(
            "tools/builtins/filesystem.py", encoding="utf-8"
        ).read()

        body = source.split("def _atomic_write")[1]

        assert "fsync" in body

    def test_the_written_bytes_are_exactly_what_was_handed_over(self, writable):

        target = writable / "raw.bin"

        _atomic_write(target, b"\x00\x01binary\xff")

        assert target.read_bytes() == b"\x00\x01binary\xff"


# ----------------------------------------------------------------------
# Section 30's habit, one level out from credentials
# ----------------------------------------------------------------------

class TestNoPathsLeakIntoTheTranscript:
    """
    Every message here goes into a prompt and so leaves the machine.
    `system_information` reports the disk but not the username for that
    reason, and an absolute path names the owner's home directory.
    """

    def test_success_messages_do_not_carry_the_home_directory(
        self, tools, writable
    ):

        home = str(Path.home())

        (writable / "log.md").write_bytes(b"x")
        (writable / "folder").mkdir()

        messages = [
            tools["write"].execute(str(writable / "n.md"), "x"),
            tools["append"].execute(str(writable / "log.md"), "y"),
            tools["mkdir"].execute(str(writable / "a" / "b")),
            tools["delete"].execute(str(writable / "n.md")),
        ]

        for message in messages:
            assert home not in message, message
            assert str(writable) not in message, message

    def test_refusals_do_not_carry_the_home_directory(self, tools, writable):

        home = str(Path.home())
        outside = writable.parent / "secret.txt"

        attempts = [
            (tools["write"].execute, (str(outside), "x")),
            (tools["delete"].execute, (str(outside),)),
            (tools["append"].execute, (str(outside), "x")),
            (tools["mkdir"].execute, (str(outside),)),
        ]

        for call, arguments in attempts:
            with pytest.raises(Exception) as raised:
                call(*arguments)

            assert home not in str(raised.value)

    def test_a_path_is_named_relative_to_its_root_with_forward_slashes(
        self, writable
    ):
        """
        So the same file reads the same way in a transcript written on
        either platform.
        """

        target = writable / "a" / "b" / "c.md"

        assert _shown(target, [writable]) == "a/b/c.md"

    def test_a_path_outside_every_root_falls_back_to_its_name(self, writable):
        """
        `_shown` is display only and must not raise on a path it cannot
        place - it is called on the way out of an error path too.
        """

        assert _shown(Path("/elsewhere/x.md"), [writable]) == "x.md"


# ----------------------------------------------------------------------
# It fits the boundary phase 16 built
# ----------------------------------------------------------------------

class TestItFitsTheExistingBoundary:

    def test_all_four_are_dangerous(self, tools):
        """
        Gate 4 asks a human at the moment of the call. Anything that
        changes the owner's disk belongs above SENSITIVE, including
        `create_directory`, which is the least destructive of the four and
        still changes the machine.
        """

        for tool in tools.values():
            assert tool.risk is ToolRisk.DANGEROUS, tool.name

    def test_all_four_satisfy_the_protocol(self, tools):

        for tool in tools.values():
            assert isinstance(tool, ToolProtocol), tool.name

    def test_none_of_them_runs_with_no_one_to_ask(self, tools, writable):
        """
        Server mode: no confirmation handler, so a DANGEROUS call is
        refused. This is the state the shipped configuration is in.
        """

        executor = executor_for(*tools.values(), approve=False)

        result = executor.execute(
            "write_file", {"path": str(writable / "n.md"), "content": "x"}
        )

        assert not result.ok
        assert not (writable / "n.md").exists()

    def test_a_refused_confirmation_writes_nothing(self, tools, writable):

        registry = ToolRegistry()
        registry.register(tools["write"])

        executor = ToolExecutor(
            registry=registry,
            policy=ToolPolicy(
                enabled=True, allowed=["write_file"], auto_approve=["safe"]
            ),
            confirm=lambda tool, arguments: False,
        )

        result = executor.execute(
            "write_file", {"path": str(writable / "n.md"), "content": "x"}
        )

        assert not result.ok
        assert not (writable / "n.md").exists()

    def test_a_tool_not_in_allowed_cannot_run(self, tools, writable):

        executor = executor_for(*tools.values(), allowed=["write_file"])

        result = executor.execute("delete_file", {"path": str(writable / "x")})

        assert not result.ok
        assert "not allowed" in result.error

    def test_a_denial_reaches_the_caller_as_a_result_not_a_crash(
        self, tools, writable
    ):
        """
        `_contained` raises PermissionError; the executor turns that into
        a failed ToolResult, so a bad path is a denial rather than a
        traceback in the middle of a conversation.
        """

        executor = executor_for(tools["write"])

        result = executor.execute(
            "write_file",
            {"path": str(writable.parent / "secret.txt"), "content": "x"},
        )

        assert not result.ok
        assert result.error

    def test_each_describes_its_parameters(self, tools):

        for tool in tools.values():

            description = tool.describe()

            assert tool.name in description
            assert "path" in description

    def test_overwrite_is_optional_and_the_rest_are_not(self, tools):

        required = tools["write"].required_parameters()

        assert "path" in required
        assert "content" in required
        assert "overwrite" not in required


# ----------------------------------------------------------------------
# The shipped configuration grants nothing
# ----------------------------------------------------------------------

class TestTheShippedConfigGrantsNothing:
    """
    Read from disk rather than through the loader, so a defaulting bug in
    the loader cannot hide a silent enable. Section 2 cuts both ways: the
    owner must be able to switch these on, and must not find them already
    on.
    """

    @staticmethod
    def shipped():
        return yaml.safe_load(
            io.open("config.yaml", encoding="utf-8")
        )["tools"]

    def test_no_writable_paths_are_shipped(self):

        assert self.shipped()["writable_paths"] == []

    def test_writable_paths_is_present_and_is_a_list(self):
        """
        Present, so the owner can see the switch exists and does not have
        to discover the key name from source. `applications: []` was
        exactly this failure in reverse (AURA-P0-004): a section with the
        wrong shape is invisible, because an empty list and an empty
        mapping are both falsy.
        """

        value = self.shipped()["writable_paths"]

        assert isinstance(value, list)

    def test_no_writer_is_in_allowed(self):

        allowed = self.shipped()["allowed"]

        for name in (
            "write_file", "append_to_file", "create_directory", "delete_file"
        ):
            assert name not in allowed

    def test_dangerous_is_not_auto_approved(self):

        assert self.shipped()["auto_approve"] == ["safe"]

    def test_the_shipped_config_registers_no_writer(self):
        """
        The end-to-end version of the three above: build the registry from
        the real file and check what came out.
        """

        registry = build_registry(self.shipped())

        for name in (
            "write_file", "append_to_file", "create_directory", "delete_file"
        ):
            assert not registry.has(name)

    def test_the_default_config_also_ships_it_empty(self):
        """
        `config.yaml` and `core/config.py` are two places the default can
        be wrong, and only one of them is what a fresh install without a
        config file uses.
        """

        from core.config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["tools"]["writable_paths"] == []

    def test_writable_paths_is_not_settable_over_the_wire(self):
        """
        The same decision `tools.commands` got in 18.2, for the same
        reason. A settable writable root would let anything holding the
        bearer token add `C:/` and then write anywhere - filesystem access
        reached around the tool boundary through the settings API instead
        of through it. Section 2's own limit: owner configuration freedom
        does not extend to letting an LLM bypass application-level
        permission boundaries.
        """

        from core.settings_store import ALLOWED

        assert "tools.writable_paths" not in ALLOWED
        assert not any(p.startswith("tools.writable_paths") for p in ALLOWED)


# ----------------------------------------------------------------------
# The factory gate
# ----------------------------------------------------------------------

class TestTheFactoryGate:

    def test_an_empty_list_registers_nothing(self):

        registry = build_registry({"writable_paths": []})

        assert not registry.has("write_file")

    def test_a_wrong_shaped_section_is_ignored_with_a_warning(self, caplog):
        """
        `writable_paths: {}` or a bare string is a mistake that would
        otherwise be invisible - the tools simply would not appear and
        nothing would say why. This is AURA-P0-004's shape.
        """

        import logging

        with caplog.at_level(logging.WARNING):
            registry = build_registry({"writable_paths": "C:/notes"})

        assert not registry.has("write_file")
        assert any("writable_paths" in r.message for r in caplog.records)

    def test_a_missing_section_is_not_a_warning(self, caplog):
        """
        A stock server has no `writable_paths` at all, and warning about
        it every startup would report a problem the owner does not have in
        a log where a real one has to stand out.
        """

        import logging

        with caplog.at_level(logging.WARNING):
            build_registry({})

        assert not any("writable_paths" in r.message for r in caplog.records)

    def test_all_four_share_one_root_list(self, writable):
        """
        Not four independently resolved copies. Two resolutions of the
        same list could disagree - one root dropped as unresolvable in one
        tool and kept in another - and the owner would see writing
        permitted in a folder deletion refuses.
        """

        registry = build_registry({"writable_paths": [str(writable)]})

        roots = [
            registry.get(name).roots
            for name in (
                "write_file", "append_to_file",
                "create_directory", "delete_file",
            )
        ]

        assert all(r == roots[0] for r in roots)
        assert roots[0] == [writable.resolve()]

    def test_the_readers_still_come_from_allowed_paths(self, writable):
        """
        A regression guard on the pre-existing half: adding the write
        block must not have moved or re-gated the readers.
        """

        registry = build_registry({"allowed_paths": [str(writable)]})

        assert registry.has("read_file")
        assert registry.has("list_directory")

    def test_both_sections_together_register_six(self, writable):

        registry = build_registry({
            "allowed_paths": [str(writable)],
            "writable_paths": [str(writable)],
        })

        for name in (
            "read_file", "list_directory", "write_file",
            "append_to_file", "create_directory", "delete_file",
        ):
            assert registry.has(name), name


# ----------------------------------------------------------------------
# The readers were not disturbed
# ----------------------------------------------------------------------

class TestTheReadersStillWork:
    """
    `_contained` gained a branch and the module docstring was rewritten.
    Both are shared with the reading tools, so the reading tools are
    re-checked here rather than assumed.
    """

    def test_reading_inside_a_root_still_works(self, writable):

        (writable / "notes.txt").write_text("inside", encoding="utf-8")

        assert ReadFileTool([str(writable)]).execute(
            str(writable / "notes.txt")
        ) == "inside"

    def test_reading_outside_is_still_refused(self, writable):

        with pytest.raises(PermissionError):
            ReadFileTool([str(writable)]).execute(
                str(writable.parent / "secret.txt")
            )

    def test_listing_still_marks_directories(self, writable):

        (writable / "sub").mkdir()
        (writable / "file.txt").write_text("x", encoding="utf-8")

        listing = ListDirectoryTool([str(writable)]).execute(str(writable))

        assert "sub/" in listing
        assert "file.txt" in listing

    def test_a_relative_read_says_it_is_relative(self, writable):
        """The new branch is shared, so the readers gained it too."""

        with pytest.raises(PermissionError) as raised:
            ReadFileTool([str(writable)]).execute("notes.txt")

        assert "relative" in str(raised.value).lower()

    def test_the_readers_have_no_verify(self, writable):
        """
        A read's postcondition is its return value, so there is nothing to
        re-ask. Absence means "execute already told the whole truth", and
        adding one here would be decoration.
        """

        for tool in (
            ReadFileTool([str(writable)]), ListDirectoryTool([str(writable)])
        ):
            assert getattr(tool, "verify", None) is None
