# YAAS - Yet Another Agent Sandbox

Run AI coding agents in sandboxed containers with proper UID passthrough.

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
- **Config persistence** - Direct mounts for `.claude`, `.gitconfig`, etc.
- **SSH agent forwarding** - Use host SSH keys inside container
- **Resource limits** - Memory, CPU, and PID limits to prevent runaway processes
- **Network isolation** - Optional `--no-network` for paranoid mode
- **Git worktrees** - Parallel development support with `yaas worktree`
- **Ephemeral clones** - Explore remote repos in isolated volumes with `--clone`

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
- **UID passthrough**: The `/etc/passwd` mount is skipped on macOS since Docker Desktop handles user mapping differently.

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

## Usage

### AI Tool Shortcuts

YAAS provides shortcuts for common AI coding agents. By default, these run with YOLO mode enabled (auto-confirm all tool calls).

```bash
# Run Claude in sandbox
yaas claude

# Pass arguments through
yaas claude -p "Fix the bug in main.py"

# Other supported tools
yaas codex
yaas gemini
yaas opencode

# Disable auto-confirm mode
yaas claude --no-yolo
```

### Argument Pass-Through

YAAS options must come first. Any unrecognized options are passed through to the underlying tool.

```bash
# YAAS options first, then tool options
yaas claude --ssh-agent --git-config --print "Fix the bug"
#           ^^^^^^^^^^^ ^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^^^^
#           yaas options             passed to claude
```

### General Commands

```bash
# Run any command in sandbox
yaas run -- make build

# Start an interactive shell
yaas shell
```

### Utility Commands

```bash
# Show current configuration
yaas config

# Manually pull the latest container image
yaas pull-image

# Manually upgrade mise-managed tools
yaas upgrade-tools

# Reset installed tools and cache (tools reinstall on next run)
yaas reset-volumes

# Remove orphaned clone volumes
yaas cleanup-clones
```

### Git Worktrees

YAAS can manage git worktrees for parallel development. This is useful when you want to run multiple agents on different branches simultaneously.

```bash
# Create a worktree with a new branch
yaas worktree add feature-x -b feature/new-thing

# List worktrees managed by YAAS
yaas worktree list

# Run Claude in a specific worktree
yaas claude --worktree feature-x

# Remove a worktree
yaas worktree remove feature-x
```

### Ephemeral Clones

The `--clone` flag clones a remote repository into an ephemeral volume for exploration. The volume is automatically removed when the container exits.

```bash
# Clone and explore with Claude
yaas claude --clone https://github.com/user/repo

# Clone a specific tag or branch
yaas claude --clone https://github.com/user/repo --ref v2.0.0

# Clone and start a shell
yaas shell --clone https://github.com/user/repo

# Clone private repo via SSH
yaas claude --clone git@github.com:org/private-repo --ssh-agent

# Secure exploration: clone first, then run without network
yaas claude --clone https://github.com/user/repo --no-network

# Run a command in the cloned repo
yaas run --clone https://github.com/user/repo -- make test
```

This is useful for:
- Quickly exploring unfamiliar codebases with AI assistance
- Running AI agents on repos you don't want to clone permanently
- Exploring untrusted code without downloading it onto the host filesystem

If a container exits unexpectedly and leaves orphaned volumes, clean them up with:

```bash
yaas cleanup-clones
```

## Configuration

YAAS uses a two-level configuration system. Global settings go in `~/.config/yaas/config.toml`, and project-specific overrides go in `.yaas.toml` at your project root. Project config takes precedence.

### All Options

```toml
# Container runtime: "podman" or "docker" (auto-detected if omitted)
runtime = "podman"

# Feature flags
ssh_agent = true           # Forward SSH agent socket
git_config = true          # Mount .gitconfig and .config/git
ai_config = true           # Mount AI tool configs (.claude, .codex, .gemini, etc.)
container_socket = false   # Mount docker/podman socket for docker-in-docker
clipboard = false          # Enable clipboard access for image pasting

# Isolation
no_network = false         # Disable network entirely
readonly_project = false   # Mount project directory as read-only

# Auto-update behavior
auto_pull_image = true     # Pull container image on startup
auto_upgrade_tools = true  # Run mise upgrade on startup

# Security
forward_api_keys = true    # Forward API keys (ANTHROPIC_API_KEY, etc.) to container

# Resource limits
[resources]
memory = "8g"              # Memory limit (e.g., "4g", "512m")
memory_swap = "8g"         # Swap limit (set equal to memory to disable swap)
cpus = 4.0                 # CPU limit
pids_limit = 1000          # Maximum number of processes (prevents fork bombs)

# Custom mounts in "source:target:mode" format
mounts = [
    "~/datasets:/data:ro",
    "/var/log/app:/logs"
]

# Custom environment variables
[env]
MY_VAR = "value"
```

### Environment Variables

The following API keys are automatically forwarded to the container if they're set in your environment (disable with `forward_api_keys = false`):

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY` / `GOOGLE_API_KEY`
- `GITHUB_TOKEN` / `GH_TOKEN`
- `COPILOT_GITHUB_TOKEN`
- `OPENROUTER_API_KEY`

## Mise Integration

YAAS uses [mise](https://mise.jdx.dev/) to manage tools inside the container. This means you can install, upgrade, or remove tools without rebuilding the container image.

### How It Works

On first run, YAAS creates a default mise configuration at `~/.config/yaas/mise.toml`. Tool installations are stored in named volumes (`yaas-data` and `yaas-cache`), so they persist across sessions.

By default, YAAS runs `mise upgrade` on each container start to keep tools current. You can disable this by setting `auto_upgrade_tools = false` in your config.

### Default Tools

The default configuration includes:

- **Runtimes:** node, python
- **Package managers:** uv
- **CLI utilities:** ripgrep, fd, fzf, jq, bat, delta
- **AI tools:** claude-code
- **Container tools:** docker-cli

### Customizing Tools

Edit `~/.config/yaas/mise.toml` to change which tools are available:

```toml
[tools]
node = "latest"
python = "latest"
uv = "latest"

# Add additional tools
go = "1.22"
rust = "stable"

# AI tools
"npm:@anthropic-ai/claude-code" = "latest"
"aqua:anomalyco/opencode" = "latest"
# "npm:@openai/codex" = "latest"
# "npm:@google/gemini-cli" = "latest"
```

### Managing Tools

```bash
# Manually upgrade all tools
yaas upgrade-tools

# Reset all tools (reinstalls on next run)
yaas reset-volumes
```

## How It Works

### UID Passthrough (Linux)

Traditional container sandboxes run processes as root or a fixed UID, which causes file permission problems when the container writes to mounted directories. On **Linux**, YAAS mounts `/etc/passwd` and `/etc/group` from the host and runs the container process with your actual UID:

```
Host (Linux)                      Container
──────────────────────────────────────────────────────────────
/etc/passwd (ro) ─────────────────> /etc/passwd
/etc/group (ro)  ─────────────────> /etc/group
--user 1000:1000 ─────────────────> Process runs as UID 1000

~/projects/myapp ─────────────────> ~/projects/myapp (rw)
                                    ↳ Files created with UID 1000 ✓
```

This means files created inside the container have correct ownership on the host. Config files like `.gitconfig` and `.claude` can be mounted directly instead of copied. Container sockets also work properly for docker-in-docker scenarios.

**Note:** On macOS, Docker Desktop handles file ownership through its file sharing layer (VirtioFS), so files created in containers should be owned by your macOS user without explicit UID passthrough. YAAS skips the `/etc/passwd` mount on macOS since Docker Desktop manages this differently.

### Persistent Volumes

YAAS uses named volumes to persist data across container sessions:

- `yaas-data` stores mise tool installations (`~/.local/share/mise`)
- `yaas-cache` stores general cache data (`~/.cache`)

This is why tools installed via mise don't need to be reinstalled every time you start a new container. Running `yaas reset-volumes` deletes these volumes, which will trigger a fresh tool installation on the next run.

## Clipboard and Image Pasting

The `--clipboard` flag enables clipboard access for pasting images into AI agents. This works by mounting display server sockets into the container.

### How It Works

| Platform | Method | Image Support |
|----------|--------|---------------|
| Linux (Wayland) | Mount Wayland socket | `wl-paste --type image/png` |
| Linux (X11) | Mount X11 socket | `xclip -t image/png -o` |
| macOS | Not supported | See workaround below |
| Windows/WSL2 | WSLg provides X11 | Works if WSLg is enabled |

### macOS Workaround

Since macOS doesn't have display server sockets, direct clipboard access isn't possible. To paste images:

1. Save the image to a file on macOS
2. Mount the directory: `yaas claude --mount ~/Downloads:/downloads`
3. Reference the image at `/downloads/image.png` in your prompt

## Security Considerations

YAAS provides filesystem and resource isolation, but it intentionally mounts sensitive files to make AI agents useful. You should understand what you're exposing:

- **AI configs** (`ai_config`): Mounts `.claude`, `.codex`, `.gemini`, and similar directories. These may contain conversation history, cached credentials, or API keys.
- **Git config** (`git_config`): Mounts `.gitconfig` which may include credentials or credential helpers.
- **SSH agent** (`ssh_agent`): Forwards your SSH agent socket. The agent can use your SSH keys to authenticate to remote servers.
- **Container socket** (`container_socket`): Mounts the Docker/Podman socket. This effectively gives the container root-equivalent access to your system.
- **API keys** (`forward_api_keys`): Environment variables like `ANTHROPIC_API_KEY` are forwarded by default. The agent can use these to make API calls. Set `forward_api_keys = false` to disable.

The sandbox prevents the agent from accessing arbitrary files on your system, but anything you mount is fully accessible. If you're running untrusted code, consider disabling these options.

## License

MIT
