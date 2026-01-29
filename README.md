# YAAS - Yet Another Agent Sandbox

Run AI coding agents in sandboxed containers with proper UID passthrough.

## Features

- **Host UID/GID passthrough** - Files created in containers have correct ownership
- **Podman first, Docker fallback** - Better rootless security model
- **AI CLI shortcuts** - `yaas claude`, `yaas codex`, etc. with YOLO mode
- **Config persistence** - Direct mounts for `.claude`, `.gitconfig`, etc.
- **SSH agent forwarding** - Use host SSH keys inside container
- **Resource limits** - Memory, CPU, and PID limits to prevent runaway processes
- **Network isolation** - Optional `--no-network` for paranoid mode

## Installation

```bash
pip install yaas

# Or with pipx for isolated install
pipx install yaas
```

## Usage

```bash
# Run Claude in sandbox with YOLO mode (auto-confirm)
yaas claude

# Run Claude with extra args
yaas claude -p "Fix the bug in main.py"

# Run any command
yaas run -- make build

# Start interactive shell
yaas shell

# With options
yaas --ssh-agent --git-config claude
```

## Configuration

Create `~/.config/yaas/config.toml` for global settings:

```toml
image = "ghcr.io/arahizzz/yaas-runtime:latest"
ssh_agent = true
git_config = true
claude_config = true

[resources]
memory = "8g"
cpus = 4.0
```

Create `.yaas.toml` in your project for project-specific overrides:

```toml
no_network = true
mounts = ["~/datasets:/data:ro"]
```

## How It Works

Unlike traditional container sandboxing that uses a fixed container user:

```
Host                              Container
──────────────────────────────────────────────────────────────
/etc/passwd (ro) ─────────────────> /etc/passwd
/etc/group (ro)  ─────────────────> /etc/group
--user 1000:1000 ─────────────────> Process runs as UID 1000

~/projects/myapp ─────────────────> ~/projects/myapp (rw)
                                    ↳ Files created with UID 1000 ✓
```

This means:
- Files created inside the container have the correct host ownership
- Config files can be mounted directly (not copied)
- Container sockets work for docker-in-docker scenarios

## License

MIT
