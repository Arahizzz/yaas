"""Git worktree wrapper functions for YAAS."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .constants import WORKTREES_DIR


class WorktreeError(Exception):
    """Error during worktree operation."""


def get_git_root(project_dir: Path | None = None) -> Path:
    """Get the root directory of the git repository (or worktree)."""
    cmd = ["git", "rev-parse", "--show-toplevel"]
    cwd = str(project_dir) if project_dir else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(f"Not a git repository: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def get_main_repo_root(project_dir: Path | None = None) -> Path:
    """Get the root of the main repository (not worktree).

    For a worktree, this returns the main repo that contains the shared .git directory.
    For a main repo, this returns the same as get_git_root.
    """
    cmd = ["git", "rev-parse", "--git-common-dir"]
    cwd = str(project_dir) if project_dir else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(f"Not a git repository: {result.stderr.strip()}")
    # --git-common-dir returns the .git directory, parent is the repo root
    git_common_dir = Path(result.stdout.strip())
    # Handle both absolute and relative paths
    if not git_common_dir.is_absolute():
        git_common_dir = (Path(cwd) / git_common_dir).resolve() if cwd else git_common_dir.resolve()
    return git_common_dir.parent


def get_project_hash(project_dir: Path | None = None) -> str:
    """SHA256 hash of git repo root, first 12 chars."""
    git_root = get_git_root(project_dir)
    hash_digest = hashlib.sha256(str(git_root).encode()).hexdigest()
    return hash_digest[:12]


def get_worktree_base_dir(
    project_dir: Path | None = None, *, main_repo: Path | None = None
) -> Path:
    """Return WORKTREES_DIR / project_hash.

    Args:
        project_dir: Project directory to resolve main repo from.
        main_repo: Pre-resolved main repo root to avoid a redundant subprocess call.
    """
    if main_repo is None:
        main_repo = get_main_repo_root(project_dir)
    hash_digest = hashlib.sha256(str(main_repo).encode()).hexdigest()[:12]
    return WORKTREES_DIR / hash_digest


def add_worktree(name: str, branch: str | None = None, project_dir: Path | None = None) -> Path:
    """Create worktree via 'git worktree add'. Returns worktree path."""
    base_dir = get_worktree_base_dir(project_dir)
    worktree_path = base_dir / name

    # Ensure parent directory exists
    base_dir.mkdir(parents=True, exist_ok=True)

    # Build git worktree add command
    cmd = ["git", "worktree", "add"]
    if branch:
        cmd.extend(["-b", branch])
    cmd.append(str(worktree_path))
    if branch:
        # When creating a new branch, specify the start point (HEAD)
        cmd.append("HEAD")

    cwd = str(project_dir) if project_dir else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(f"Failed to create worktree: {result.stderr.strip()}")

    return worktree_path


def list_worktrees(project_dir: Path | None = None) -> list[dict[str, str]]:
    """Parse 'git worktree list --porcelain' output.

    Returns list of dicts with keys: path, head, branch (optional)
    """
    cmd = ["git", "worktree", "list", "--porcelain"]
    cwd = str(project_dir) if project_dir else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(f"Failed to list worktrees: {result.stderr.strip()}")

    worktrees = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue

        if line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = "true"
        elif line == "detached":
            current["detached"] = "true"

    if current:
        worktrees.append(current)

    return worktrees


def remove_worktree(name: str, force: bool = False, project_dir: Path | None = None) -> None:
    """Remove worktree via 'git worktree remove'."""
    worktree_path = get_worktree_path(name, project_dir)
    if worktree_path is None:
        raise WorktreeError(f"Worktree '{name}' not found")

    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))

    cwd = str(project_dir) if project_dir else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(f"Failed to remove worktree: {result.stderr.strip()}")


def get_worktree_path(name: str, project_dir: Path | None = None) -> Path | None:
    """Look up worktree path by name from git worktree list.

    Returns the path if found, None otherwise.
    """
    base_dir = get_worktree_base_dir(project_dir).resolve()
    expected_path = base_dir / name

    worktrees = list_worktrees(project_dir)
    for wt in worktrees:
        if Path(wt["path"]).resolve() == expected_path:
            return Path(wt["path"])

    return None


def repair_worktrees(project_dir: Path | None = None) -> list[str]:
    """Fix paths after project move.

    1. Get worktree paths from 'git worktree list --porcelain'
    2. Check if any are under old hash dir in WORKTREES_DIR
    3. Move to new hash dir
    4. Run 'git worktree repair'

    Returns list of messages describing actions taken.
    """
    messages: list[str] = []
    current_hash = get_project_hash(project_dir)
    current_base = WORKTREES_DIR.resolve() / current_hash
    worktrees_dir_resolved = WORKTREES_DIR.resolve()

    # Get current worktrees from git (it still knows about them even if paths changed)
    worktrees = list_worktrees(project_dir)

    # Find worktrees that are in WORKTREES_DIR but under a different hash
    for wt in worktrees:
        wt_path = Path(wt["path"]).resolve()

        # Skip main worktree (not under WORKTREES_DIR)
        if not str(wt_path).startswith(str(worktrees_dir_resolved)):
            continue

        # Check if this worktree is under an old hash directory
        try:
            relative = wt_path.relative_to(worktrees_dir_resolved)
            old_hash = relative.parts[0]
            worktree_name = relative.parts[1] if len(relative.parts) > 1 else None
        except (ValueError, IndexError):
            continue

        if old_hash == current_hash:
            # Already in correct location
            continue

        if worktree_name is None:
            continue

        # Move worktree from old location to new location
        old_path = worktrees_dir_resolved / old_hash / worktree_name
        new_path = current_base / worktree_name

        if old_path.exists():
            current_base.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)
            messages.append(f"Moved worktree '{worktree_name}' from {old_hash} to {current_hash}")

    # Run git worktree repair to fix internal pointers
    cmd = ["git", "worktree", "repair"]
    cwd = str(project_dir) if project_dir else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    if result.returncode != 0:
        raise WorktreeError(f"Failed to repair worktrees: {result.stderr.strip()}")

    if result.stdout.strip():
        messages.append(result.stdout.strip())

    # Clean up empty old hash directories
    if worktrees_dir_resolved.exists():
        for hash_dir in worktrees_dir_resolved.iterdir():
            if hash_dir.is_dir() and hash_dir.name != current_hash:
                # Check if directory is empty
                if not any(hash_dir.iterdir()):
                    hash_dir.rmdir()
                    messages.append(f"Removed empty directory for old hash {hash_dir.name}")

    return messages


def get_yaas_worktrees(project_dir: Path | None = None) -> list[dict[str, str]]:
    """Get worktrees that are managed by YAAS (under WORKTREES_DIR).

    Returns list of dicts with keys: name, path, head, branch (optional)
    """
    base_dir = get_worktree_base_dir(project_dir).resolve()
    all_worktrees = list_worktrees(project_dir)

    yaas_worktrees = []
    for wt in all_worktrees:
        wt_path = Path(wt["path"]).resolve()
        if str(wt_path).startswith(str(base_dir)):
            # Extract worktree name from path
            wt["name"] = wt_path.name
            yaas_worktrees.append(wt)

    return yaas_worktrees


def check_worktree_in_use(worktree_path: Path, command_prefix: list[str]) -> bool:
    """Check if a worktree is currently mounted in a running container.

    Queries the container runtime to check if any container has the worktree path mounted.
    """
    cmd = [*command_prefix, "ps", "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # Runtime might not be available, can't check
        return False

    import json

    try:
        # podman ps --format json returns a JSON array
        containers = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError:
        return False

    worktree_str = str(worktree_path)
    for container in containers:
        # Check Mounts field for podman/docker
        mounts = container.get("Mounts", [])
        for mount in mounts:
            # Mount can be a string or dict depending on runtime
            if isinstance(mount, str):
                if worktree_str in mount:
                    return True
            elif isinstance(mount, dict):
                if worktree_str in mount.get("Source", ""):
                    return True

    return False
