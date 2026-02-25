"""Tests for shell completion callbacks."""

from unittest.mock import patch

from yaas.completions import NetworkMode, complete_worktree


def test_network_mode_values() -> None:
    """NetworkMode enum has the expected values."""
    assert NetworkMode.host.value == "host"
    assert NetworkMode.bridge.value == "bridge"
    assert NetworkMode.none.value == "none"


def test_network_mode_string_compatibility() -> None:
    """NetworkMode values are usable as plain strings."""
    assert NetworkMode.bridge == "bridge"
    assert isinstance(NetworkMode.host, str)


def test_complete_worktree_returns_names() -> None:
    """complete_worktree returns worktree names with branch info."""
    mock_worktrees = [
        {"name": "feat-a", "branch": "refs/heads/feature/a"},
        {"name": "fix-b", "branch": "refs/heads/fix/b"},
    ]
    with patch("yaas.worktree.get_yaas_worktrees", return_value=mock_worktrees):
        results = complete_worktree("")

    assert len(results) == 2
    assert results[0] == ("feat-a", "branch: feature/a")
    assert results[1] == ("fix-b", "branch: fix/b")


def test_complete_worktree_filters_by_prefix() -> None:
    """complete_worktree filters results by incomplete prefix."""
    mock_worktrees = [
        {"name": "feat-a", "branch": "refs/heads/feature/a"},
        {"name": "feat-b", "branch": "refs/heads/feature/b"},
        {"name": "fix-c", "branch": "refs/heads/fix/c"},
    ]
    with patch("yaas.worktree.get_yaas_worktrees", return_value=mock_worktrees):
        results = complete_worktree("feat")

    assert len(results) == 2
    names = [r[0] for r in results]
    assert "feat-a" in names
    assert "feat-b" in names


def test_complete_worktree_handles_errors() -> None:
    """complete_worktree returns empty list on any error."""
    with patch("yaas.worktree.get_yaas_worktrees", side_effect=RuntimeError("fail")):
        results = complete_worktree("")

    assert results == []


def test_complete_worktree_empty_list() -> None:
    """complete_worktree returns empty list when no worktrees exist."""
    with patch("yaas.worktree.get_yaas_worktrees", return_value=[]):
        results = complete_worktree("")

    assert results == []


def test_complete_worktree_no_branch() -> None:
    """complete_worktree handles worktrees without branch info."""
    mock_worktrees = [{"name": "detached-wt"}]
    with patch("yaas.worktree.get_yaas_worktrees", return_value=mock_worktrees):
        results = complete_worktree("")

    assert results == [("detached-wt", "")]
