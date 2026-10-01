"""
Unit tests for safe Workspace and Git pair-programming tools.
"""

from pathlib import Path
import pytest

from tools.builtins.workspace import (
    PROJECT_ROOT,
    WorkspaceGitDiffTool,
    WorkspaceGitStatusTool,
    WorkspaceSearchFilesTool,
    _verify_safe_workspace_path,
)
from tools.base import ToolRisk, SideEffect


def test_verify_safe_workspace_path():
    allowed_roots = [PROJECT_ROOT]

    # Valid relative path
    safe, target, reason = _verify_safe_workspace_path("server/main.py", allowed_roots)
    assert safe
    assert target == (PROJECT_ROOT / "server" / "main.py").resolve()
    assert reason == ""

    # Valid absolute path inside root
    safe, target, reason = _verify_safe_workspace_path(str(PROJECT_ROOT / "config.yaml"), allowed_roots)
    assert safe
    assert reason == ""

    # Path traversal outside root
    safe, target, reason = _verify_safe_workspace_path("../../secret.txt", allowed_roots)
    assert not safe
    assert "outside" in reason.lower()

    # Empty path
    safe, target, reason = _verify_safe_workspace_path("   ", allowed_roots)
    assert not safe
    assert "empty" in reason.lower()


def test_workspace_git_status_tool_attributes():
    tool = WorkspaceGitStatusTool()
    assert tool.name == "workspace_git_status"
    assert tool.capability == "workspace.git"
    assert tool.risk == ToolRisk.SAFE
    assert tool.side_effect == SideEffect.READ_ONLY


def test_workspace_git_status_live_execution():
    tool = WorkspaceGitStatusTool()
    res = tool.execute()
    assert res.ok
    assert "Workspace Git Status" in res.output
    assert "Branch:" in res.output
    assert "Latest Commit:" in res.output
    assert "branch" in res.data
    assert "staged_count" in res.data
    assert "unstaged_count" in res.data
    assert "untracked_count" in res.data


def test_workspace_git_diff_tool_attributes():
    tool = WorkspaceGitDiffTool()
    assert tool.name == "workspace_git_diff"
    assert tool.capability == "workspace.git"
    assert tool.risk == ToolRisk.SAFE
    assert tool.side_effect == SideEffect.READ_ONLY


def test_workspace_git_diff_path_outside_rejected():
    tool = WorkspaceGitDiffTool()
    res = tool.execute(filepath="../../outside.txt")
    assert not res.ok
    assert "outside" in res.error.lower()


def test_workspace_git_diff_execution():
    tool = WorkspaceGitDiffTool()
    res = tool.execute(max_lines=50)
    assert res.ok
    assert "lines" in res.data or "total_lines" in res.data


def test_workspace_search_files_tool_attributes():
    tool = WorkspaceSearchFilesTool()
    assert tool.name == "workspace_search_files"
    assert tool.capability == "workspace.search"
    assert tool.risk == ToolRisk.SAFE
    assert tool.side_effect == SideEffect.READ_ONLY


def test_workspace_search_files_empty_pattern():
    tool = WorkspaceSearchFilesTool()
    res = tool.execute(pattern="")
    assert not res.ok
    assert "pattern" in res.error.lower()


def test_workspace_search_files_finds_known_file():
    tool = WorkspaceSearchFilesTool()
    res = tool.execute(pattern="workspace.py", file_extension=".py", max_results=10)
    assert res.ok
    assert "workspace.py" in res.output
    matches = res.data.get("matches", [])
    assert any("workspace.py" in m["path"].replace("\\", "/") for m in matches)
    # Ensure ignored folders like .venv or .git didn't leak
    assert all(".git" not in m["path"].split("/") for m in matches)
    assert all(".venv" not in m["path"].split("/") for m in matches)


def test_workspace_search_files_search_dir_outside():
    tool = WorkspaceSearchFilesTool()
    res = tool.execute(pattern="test", search_dir="../../outside_dir")
    assert not res.ok
    assert "outside" in res.error.lower()
