"""Tests for worktree module."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest

from yaas.worktree import (
    WorktreeError,
    add_worktree,
    check_worktree_in_use,
    get_git_root,
    get_project_hash,
    get_worktree_base_dir,
    get_worktree_path,
    get_yaas_worktrees,
    list_worktrees,
    remove_worktree,
    repair_worktrees,
)


@pytest.fixture
def git_repo():
    """Create a temporary git repository for testing."""
    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test-repo"
        repo_path.mkdir()
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            capture_output=True,
        )
        # Create initial commit
        (repo_path / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            capture_output=True,
        )
        yield repo_path


def test_get_git_root(git_repo: Path) -> None:
    """Test getting git repository root."""
    root = get_git_root(git_repo)
    assert root == git_repo


def test_get_git_root_not_a_repo() -> None:
    """Test error when not in a git repository."""
    with TemporaryDirectory() as tmpdir:
        with pytest.raises(WorktreeError, match="Not a git repository"):
            get_git_root(Path(tmpdir))


def test_get_project_hash(git_repo: Path) -> None:
    """Test project hash generation."""
    hash1 = get_project_hash(git_repo)
    # Hash should be 12 hex characters
    assert len(hash1) == 12
    assert all(c in "0123456789abcdef" for c in hash1)

    # Same path should give same hash
    hash2 = get_project_hash(git_repo)
    assert hash1 == hash2


def test_get_project_hash_different_repos() -> None:
    """Test that different repos get different hashes."""
    with TemporaryDirectory() as tmpdir1, TemporaryDirectory() as tmpdir2:
        repo1 = Path(tmpdir1) / "repo1"
        repo2 = Path(tmpdir2) / "repo2"
        repo1.mkdir()
        repo2.mkdir()

        for repo in [repo1, repo2]:
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                capture_output=True,
            )
            (repo / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=repo,
                capture_output=True,
            )

        hash1 = get_project_hash(repo1)
        hash2 = get_project_hash(repo2)
        assert hash1 != hash2


def test_get_worktree_base_dir(git_repo: Path) -> None:
    """Test getting worktree base directory."""
    from yaas.constants import WORKTREES_DIR

    base_dir = get_worktree_base_dir(git_repo)
    project_hash = get_project_hash(git_repo)

    assert base_dir == WORKTREES_DIR / project_hash


def test_list_worktrees(git_repo: Path) -> None:
    """Test listing worktrees."""
    worktrees = list_worktrees(git_repo)

    # Should have at least the main worktree
    assert len(worktrees) >= 1
    assert worktrees[0]["path"] == str(git_repo)


def test_list_worktrees_porcelain_parsing() -> None:
    """Test parsing of git worktree list --porcelain output."""
    porcelain_output = """worktree /path/to/main
HEAD abc123def456
branch refs/heads/main

worktree /path/to/feature
HEAD def456abc789
branch refs/heads/feature

worktree /path/to/detached
HEAD 111222333444
detached

"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        worktrees = list_worktrees(Path("/fake/repo"))

    assert len(worktrees) == 3
    assert worktrees[0]["path"] == "/path/to/main"
    assert worktrees[0]["branch"] == "refs/heads/main"
    assert worktrees[1]["path"] == "/path/to/feature"
    assert worktrees[2]["detached"] == "true"


def test_add_worktree(git_repo: Path) -> None:
    """Test creating a worktree."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        worktree_path = add_worktree("test-wt", project_dir=git_repo)

        assert worktree_path.exists()
        assert worktree_path.name == "test-wt"

        # Verify git knows about it
        worktrees = list_worktrees(git_repo)
        paths = [wt["path"] for wt in worktrees]
        assert str(worktree_path) in paths


def test_add_worktree_with_branch(git_repo: Path) -> None:
    """Test creating a worktree with a new branch."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        worktree_path = add_worktree("feature-wt", branch="feature/test", project_dir=git_repo)

        assert worktree_path.exists()

        # Verify the branch was created
        result = subprocess.run(
            ["git", "branch", "--list", "feature/test"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert "feature/test" in result.stdout


def test_get_worktree_path(git_repo: Path) -> None:
    """Test getting worktree path by name."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        # Create a worktree first
        add_worktree("lookup-test", project_dir=git_repo)

        # Should find it
        path = get_worktree_path("lookup-test", git_repo)
        assert path is not None
        assert path.name == "lookup-test"

        # Should not find non-existent
        path = get_worktree_path("nonexistent", git_repo)
        assert path is None


def test_remove_worktree(git_repo: Path) -> None:
    """Test removing a worktree."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        # Create then remove
        add_worktree("remove-test", project_dir=git_repo)

        remove_worktree("remove-test", project_dir=git_repo)

        # Should no longer be found
        path = get_worktree_path("remove-test", git_repo)
        assert path is None


def test_remove_worktree_not_found(git_repo: Path) -> None:
    """Test error when removing non-existent worktree."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        with pytest.raises(WorktreeError, match="not found"):
            remove_worktree("nonexistent", project_dir=git_repo)


def test_get_yaas_worktrees(git_repo: Path) -> None:
    """Test getting only YAAS-managed worktrees."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        # Create some worktrees
        add_worktree("yaas-wt1", project_dir=git_repo)
        add_worktree("yaas-wt2", project_dir=git_repo)

        yaas_wts = get_yaas_worktrees(git_repo)

        # Should have the two we created (not the main worktree)
        names = [wt["name"] for wt in yaas_wts]
        assert "yaas-wt1" in names
        assert "yaas-wt2" in names
        assert len(yaas_wts) == 2


def test_check_worktree_in_use_no_containers() -> None:
    """Test checking worktree usage when no containers are running."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[]",
            stderr="",
        )

        result = check_worktree_in_use(Path("/some/worktree"))
        assert result is False


def test_check_worktree_in_use_with_matching_container() -> None:
    """Test checking worktree usage when container has it mounted."""
    import json

    containers = [
        {
            "Id": "abc123",
            "Mounts": [{"Source": "/some/worktree", "Destination": "/project"}],
        }
    ]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(containers),
            stderr="",
        )

        result = check_worktree_in_use(Path("/some/worktree"))
        assert result is True


def test_check_worktree_in_use_runtime_not_available() -> None:
    """Test checking worktree usage when runtime is not available."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="command not found",
        )

        # Should return False (can't check, so assume not in use)
        result = check_worktree_in_use(Path("/some/worktree"))
        assert result is False


def test_repair_worktrees_no_repairs_needed(git_repo: Path) -> None:
    """Test repair when no repairs are needed."""
    with patch("yaas.worktree.WORKTREES_DIR", git_repo.parent / "worktrees"):
        messages = repair_worktrees(git_repo)
        # Should complete without error
        assert isinstance(messages, list)
