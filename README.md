# YAAS - Yet Another Agent Sandbox

Run AI coding agents in sandboxed containers with proper UID passthrough.

> **Note:** This is an experimental personal project in which I explore how to sandbox AI agents' capabilities in an ergonomic way. I am testing various features and designs while evaluating them on my real-life use cases. As such, I am currently not committing to API stability of any kind and you should expect adding/removal/reworking of features with future updates.

## Why YAAS?

Container sandboxing for AI agents is nothing new, but most solutions have friction that makes them impractical for daily use.

**File permission problems.** When a container creates files, they're owned by root or a mismatched UID. You end up running `chown` constantly or fighting permission errors. YAAS passes through your host UID/GID so files are created with correct ownership from the start.

**Tool management is painful.** Need to install a new CLI tool? With most sandboxes you have to rebuild the container image. YAAS uses [mise](https://mise.jdx.dev/) to manage tools at runtime. Install, upgrade, or remove tools without touching a Dockerfile, and they persist across sessions in named volumes.

**No resource limits.** An agent can spawn infinite processes or consume all available memory, freezing your system. YAAS lets you set memory, CPU, and PID limits to keep things under control.

## Features

- **Host UID/GID passthrough** - Files created in containers have correct ownership
- **Podman first, Docker fallback** - Better rootless security model
- **AI CLI shortcuts** - `yaas claude`, `yaas codex`, etc. with YOLO mode
- **Mise integration** - Automatic tool version management inside containers
- **Nix packages** - Access 100k+ packages from nixpkgs via [mise-nix](https://github.com/jbadeau/mise-nix)
- **Persistent boxes** - Long-lived containers with `yaas box`, optional Podman quadlet generation
- **Config persistence** - Direct mounts for `.claude`, `.gitconfig`, etc.
- **SSH agent forwarding** - Use host SSH keys inside container
- **Resource limits** - Memory, CPU, and PID limits to prevent runaway processes
- **Network isolation** - Configurable network mode (host, bridge, none)
- **Git worktrees** - Parallel development support with `yaas worktree`

## Platform Support

| Platform | Support Level | Notes |
|----------|---------------|-------|
| Linux | Full | Primary platform, Podman or Docker |
| macOS | Experimental | Docker Desktop recommended |
| Windows | Experimental | WSL2 only |

**Note:** Linux is the primary supported platform. macOS and Windows/WSL2 support is experimental and has not been extensively tested. Contributions and bug reports for non-Linux platforms are welcome.

### macOS (Experimental)

YAAS should work on macOS with Docker Desktop, but has not been thoroughly tested. Known differences:

- **File ownership**: Docker Desktop handles file ownership through its file sharing layer (VirtioFS), so files created in containers should be owned by your macOS user.
- **Podman**: Not supported on macOS.
- **Clipboard**: Direct clipboard access is not available. Display server sockets (Wayland/X11) don't exist on macOS.
- **UID passthrough**: Docker Desktop handles user mapping through its VM layer, so explicit UID passthrough is not needed.

### Windows (Experimental)

YAAS does not support native Windows. Instead, run YAAS inside WSL2:

1. Install WSL2: `wsl --install`
2. Open a WSL2 terminal (e.g., Ubuntu)
3. Install Docker or Podman inside WSL2
4. Install YAAS: `pip install yaas` or `uv tool install yaas`

With WSLg (Windows Subsystem for Linux GUI), clipboard features work as they do on Linux.

## Installation

```bash
# Using uv (recommended)
uv tool install git+https://github.com/arahizzz/yaas

# Using pipx
pipx install git+https://github.com/arahizzz/yaas
```

Requires Podman or Docker to be installed on your system.

### Shell Completion

YAAS supports tab completion for bash, zsh, and fish. Install it with:

```bash
yaas --install-completion
```

After restarting your shell, you can tab-complete commands (`yaas clau<TAB>` → `yaas claude`), options, and option values like `--network` and `--worktree`.

## Quick Start

```bash
# Run Claude in sandbox with YOLO mode
yaas claude

# Run any command
yaas run -- make build

# Interactive shell
yaas shell

# Create a persistent box
yaas box create dev shell
yaas box enter dev

# Show resolved config
yaas config
yaas config claude
yaas box config shell --ssh-agent
```

See [docs/usage.md](docs/usage.md) for the full command reference.

## Documentation

- **[Usage](docs/usage.md)** — full command reference: AI shortcuts, worktrees, boxes, clipboard
- **[Configuration](docs/configuration.md)** — config files, tool overrides, box specs, mise integration
- **[Internals](docs/internals.md)** — UID passthrough, volumes, runtimes, security

## License

MIT
