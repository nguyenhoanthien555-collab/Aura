"""
Safe Workspace and Git pair-programming tools for Aura.

Provides safe, read-only inspection of the project workspace and repository state:
1. WorkspaceGitStatusTool (`workspace_git_status`): Branch, ahead/behind, staged/unstaged changes, and recent commit.
2. WorkspaceGitDiffTool (`workspace_git_diff`): Bounded code diffs for files or whole tree.
3. WorkspaceSearchFilesTool (`workspace_search_files`): Fast, noise-filtered file and pattern search.

All tools enforce strict containment to PROJECT_ROOT and configured allowed paths.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Optional

from core.logger import logger
from tools.base import Parameter, SideEffect, Tool, ToolResult, ToolRisk, fail, ok

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
IGNORED_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".venv",
    ".codegraph",
    ".system_generated",
    "node_modules",
    "__pycache__",
    "build",
    "test_tmp",
    ".pytest_cache",
}


def _resolve_workspace_roots(extra_roots: Optional[list[str]] = None) -> list[Path]:
    """Resolve and normalize workspace root directories."""
    roots = [PROJECT_ROOT]
    for r in extra_roots or []:
        try:
            p = Path(r).expanduser().resolve()
            if p.exists() and p.is_dir() and p not in roots:
                roots.append(p)
        except Exception:
            continue
    return roots


def _verify_safe_workspace_path(
    path_str: str,
    allowed_roots: list[Path],
    base_root: Optional[Path] = None,
) -> tuple[bool, Path, str]:
    """
    Ensure the path is contained within one of the allowed workspace roots.
    Relative paths are resolved against base_root (defaulting to PROJECT_ROOT).
    """
    if not path_str or not str(path_str).strip():
        return False, Path("."), "Path must not be empty."

    base = base_root or PROJECT_ROOT
    try:
        raw_path = Path(path_str.strip()).expanduser()
        if not raw_path.is_absolute():
            target = (base / raw_path).resolve()
        else:
            target = raw_path.resolve()
    except Exception as err:
        return False, Path("."), f"Invalid path syntax '{path_str}': {err}"

    for root in allowed_roots:
        try:
            target.relative_to(root)
            return True, target, ""
        except ValueError:
            continue

    return False, target, f"Access denied: path '{path_str}' is outside allowed workspace boundaries."


class WorkspaceGitStatusTool(Tool):
    """
    Inspect the current Git repository status (branch, commits, staged, modified, and untracked files).
    """

    name = "workspace_git_status"
    description = (
        "Inspect the current Git repository status of the workspace: current branch, "
        "tracking branch, ahead/behind commit counts, and staged/modified/untracked files."
    )
    capability = "workspace.git"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = []

    def __init__(self, allowed_roots: Optional[list[str]] = None):
        self.roots = _resolve_workspace_roots(allowed_roots)

    def execute(self, **kwargs: Any) -> ToolResult:
        repo_dir = self.roots[0]
        if not (repo_dir / ".git").exists():
            return fail(f"Workspace root '{repo_dir}' is not a Git repository.", tool=self.name)

        try:
            # 1. git status --porcelain=v1 -b
            status_res = subprocess.run(
                ["git", "status", "--porcelain=v1", "-b"],
                cwd=str(repo_dir),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                check=False,
            )
            if status_res.returncode != 0:
                err_msg = (status_res.stderr or "").strip() or "git status command failed"
                return fail(f"Git status failed: {err_msg}", tool=self.name)

            # 2. git log -1
            log_res = subprocess.run(
                ["git", "log", "-1", "--format=%h - %s (%an, %cr)"],
                cwd=str(repo_dir),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                check=False,
            )
            last_commit = (log_res.stdout or "").strip() if log_res.returncode == 0 else "No commits yet"

            lines = status_res.stdout.splitlines()
            branch_info = "unknown"
            staged = []
            unstaged = []
            untracked = []

            for line in lines:
                if line.startswith("## "):
                    branch_info = line[3:].strip()
                elif line.startswith("?? "):
                    untracked.append(line[3:].strip())
                elif len(line) >= 3:
                    index_stat = line[0]
                    work_stat = line[1]
                    filename = line[3:].strip()

                    if index_stat in ("M", "A", "D", "R", "C"):
                        staged.append(f"{index_stat} {filename}")
                    if work_stat in ("M", "D"):
                        unstaged.append(f"{work_stat} {filename}")

            output_lines = [
                f"Workspace Git Status ({repo_dir.name}):",
                f"- Branch: {branch_info}",
                f"- Latest Commit: {last_commit}",
                f"- Staged Changes: {len(staged)} file(s)",
            ]
            for item in staged[:15]:
                output_lines.append(f"    {item}")
            if len(staged) > 15:
                output_lines.append(f"    ... and {len(staged) - 15} more")

            output_lines.append(f"- Unstaged Modifications: {len(unstaged)} file(s)")
            for item in unstaged[:15]:
                output_lines.append(f"    {item}")
            if len(unstaged) > 15:
                output_lines.append(f"    ... and {len(unstaged) - 15} more")

            output_lines.append(f"- Untracked Files: {len(untracked)} file(s)")
            for item in untracked[:15]:
                output_lines.append(f"    {item}")
            if len(untracked) > 15:
                output_lines.append(f"    ... and {len(untracked) - 15} more")

            summary = "\n".join(output_lines)
            data = {
                "branch": branch_info,
                "latest_commit": last_commit,
                "staged_count": len(staged),
                "unstaged_count": len(unstaged),
                "untracked_count": len(untracked),
            }
            return ok(summary, tool=self.name, data=data)

        except subprocess.TimeoutExpired:
            return fail("Git status command timed out after 5.0 seconds.", tool=self.name)
        except Exception as error:
            logger.error("WorkspaceGitStatusTool execution error: %s", error)
            return fail(f"Failed to check git status: {error}", tool=self.name)


class WorkspaceGitDiffTool(Tool):
    """
    Get a safe, bounded Git diff of changes in the workspace or for a specific file.
    """

    name = "workspace_git_diff"
    description = (
        "Get a bounded Git diff of changes in the workspace. "
        "Can inspect unstaged changes or staged changes, optionally filtered by a relative filepath."
    )
    capability = "workspace.git"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = [
        Parameter("filepath", "Optional relative filepath to inspect diff for", required=False, type="string"),
        Parameter("staged", "Set to true to inspect staged (cached) changes, false for unstaged (default false)", required=False, type="boolean"),
        Parameter("max_lines", "Maximum number of diff lines to return (10 to 500, default 200)", required=False, type="integer"),
    ]

    def __init__(self, allowed_roots: Optional[list[str]] = None):
        self.roots = _resolve_workspace_roots(allowed_roots)

    def execute(
        self,
        filepath: Optional[str] = None,
        staged: bool = False,
        max_lines: int = 200,
        **kwargs: Any,
    ) -> ToolResult:
        repo_dir = self.roots[0]
        cmd = ["git", "diff", "--no-color"]
        if staged:
            cmd.append("--cached")

        target_file_str = ""
        if filepath and str(filepath).strip():
            safe, target_path, reason = _verify_safe_workspace_path(filepath, self.roots)
            if not safe:
                return fail(reason, tool=self.name)
            try:
                rel_path = target_path.relative_to(repo_dir).as_posix()
                cmd.extend(["--", rel_path])
                target_file_str = rel_path
            except ValueError:
                return fail(f"File '{filepath}' is outside git repository root.", tool=self.name)

        try:
            limit = max(10, min(500, int(max_lines)))
        except (ValueError, TypeError):
            limit = 200

        try:
            res = subprocess.run(
                cmd,
                cwd=str(repo_dir),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                check=False,
            )
            if res.returncode != 0:
                err_msg = (res.stderr or "").strip() or "git diff failed"
                return fail(f"Git diff failed: {err_msg}", tool=self.name)

            diff_text = res.stdout or ""
            if not diff_text.strip():
                scope = f"file '{target_file_str}'" if target_file_str else "workspace"
                kind = "staged" if staged else "unstaged"
                return ok(f"No {kind} diff changes found for {scope}.", tool=self.name, data={"lines": 0})

            diff_lines = diff_text.splitlines()
            total_lines = len(diff_lines)
            truncated = total_lines > limit

            output_content = "\n".join(diff_lines[:limit])
            if truncated:
                output_content += f"\n\n[... Truncated: showing first {limit} of {total_lines} lines ...]"

            header = f"Git diff ({'staged' if staged else 'unstaged'})"
            if target_file_str:
                header += f" for {target_file_str}"
            summary = f"{header} - {total_lines} total lines:\n\n{output_content}"

            return ok(
                summary,
                tool=self.name,
                data={
                    "total_lines": total_lines,
                    "shown_lines": min(limit, total_lines),
                    "truncated": truncated,
                    "staged": staged,
                },
            )

        except subprocess.TimeoutExpired:
            return fail("Git diff command timed out after 5.0 seconds.", tool=self.name)
        except Exception as error:
            logger.error("WorkspaceGitDiffTool execution error: %s", error)
            return fail(f"Failed to run git diff: {error}", tool=self.name)


class WorkspaceSearchFilesTool(Tool):
    """
    Search for files by name, glob pattern, or extension across the workspace.
    Filters out noise directories (.git, .venv, node_modules, build, etc.).
    """

    name = "workspace_search_files"
    description = (
        "Search for files in the workspace matching a pattern or keyword. "
        "Automatically skips build artifacts, virtualenvs, and git internals."
    )
    capability = "workspace.search"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = [
        Parameter("pattern", "Filename substring or glob pattern (e.g. '*memory*', 'settings.py', 'AuraApi')", required=True, type="string"),
        Parameter("file_extension", "Optional extension filter (e.g. '.py', '.kt', '.md')", required=False, type="string"),
        Parameter("max_results", "Maximum search results to return (1 to 100, default 30)", required=False, type="integer"),
        Parameter("search_dir", "Optional subdirectory to restrict search to (e.g. 'server', 'android')", required=False, type="string"),
    ]

    def __init__(self, allowed_roots: Optional[list[str]] = None):
        self.roots = _resolve_workspace_roots(allowed_roots)

    def execute(
        self,
        pattern: str = "",
        file_extension: Optional[str] = None,
        max_results: int = 30,
        search_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolResult:
        query = (pattern or "").strip()
        if not query:
            return fail("Pattern parameter is required.", tool=self.name)

        try:
            limit = max(1, min(100, int(max_results)))
        except (ValueError, TypeError):
            limit = 30

        base_root = self.roots[0]
        start_dir = base_root
        if search_dir and str(search_dir).strip():
            safe, verified_path, reason = _verify_safe_workspace_path(search_dir, self.roots)
            if not safe:
                return fail(reason, tool=self.name)
            if not verified_path.is_dir():
                return fail(f"Search directory '{search_dir}' is not a directory.", tool=self.name)
            start_dir = verified_path

        clean_ext = (file_extension or "").strip().lower()
        if clean_ext and not clean_ext.startswith("."):
            clean_ext = f".{clean_ext}"

        matches = []
        is_glob = any(char in query for char in ("*", "?", "[", "]"))
        pattern_lower = query.lower()

        try:
            for root, dirs, files in os.walk(start_dir):
                # Prune noise directories in-place
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

                root_path = Path(root)
                for file_name in files:
                    if file_name.startswith("."):
                        continue

                    if clean_ext and not file_name.lower().endswith(clean_ext):
                        continue

                    file_name_lower = file_name.lower()
                    matched = False
                    if is_glob:
                        matched = fnmatch.fnmatch(file_name_lower, pattern_lower)
                    else:
                        matched = pattern_lower in file_name_lower

                    if matched:
                        full_file = root_path / file_name
                        try:
                            rel = full_file.relative_to(base_root).as_posix()
                        except ValueError:
                            rel = full_file.as_posix()

                        try:
                            size_bytes = full_file.stat().st_size if full_file.exists() else 0
                        except OSError:
                            size_bytes = 0
                        matches.append({"path": rel, "size": size_bytes})

                        if len(matches) >= limit:
                            break

                if len(matches) >= limit:
                    break

            if not matches:
                return ok(
                    f"No files found matching pattern '{query}' in {start_dir.name}.",
                    tool=self.name,
                    data={"matches": []},
                )

            lines = [f"Found {len(matches)} file(s) matching '{query}':\n"]
            for idx, item in enumerate(matches, 1):
                size_str = f"{item['size']:,} bytes" if item["size"] < 1024 * 1024 else f"{item['size'] / (1024*1024):.1f} MB"
                lines.append(f"{idx}. {item['path']} ({size_str})")

            summary = "\n".join(lines)
            return ok(summary, tool=self.name, data={"matches": matches})

        except Exception as error:
            logger.error("WorkspaceSearchFilesTool failed: %s", error)
            return fail(f"Search failed: {error}", tool=self.name)


__all__ = [
    "WorkspaceGitDiffTool",
    "WorkspaceGitStatusTool",
    "WorkspaceSearchFilesTool",
    "_verify_safe_workspace_path",
]
