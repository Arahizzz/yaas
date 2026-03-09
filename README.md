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
- **Nix packages** - Access 100k+ packages from nixpkgs via [mise-nix](https://github.com/jbadeau/mise-nix)
- **Config persistence** - Direct mounts for `.claude`, `.gitconfig`, etc.
- **SSH agent forwarding** - Use host SSH keys inside container
- **Resource limits** - Memory, CPU, and PID limits to prevent runaway processes
- **Network isolation** - Configurable network mode (host, bridge, none)
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

### Sandbox Awareness

AI agents running inside containers see a partial slice of the system — some paths may be missing, tools may not be pre-installed, and things may not work as expected compared to a full host environment. They may also pick up misleading information from mounted config files that reference host-specific paths or tools. Without context, agents can get confused and waste time debugging container artifacts. YAAS sets a `YAAS_PREAMBLE` environment variable that tells the agent it's in a sandbox and provides details about the environment — resource limits, network mode, mounted paths, and how to install tools. Agents that support system prompt injection receive this automatically:

- **Claude Code** uses `--append-system-prompt $YAAS_PREAMBLE` (configured by default)
- **Codex** uses `-c developer_instructions=$YAAS_PREAMBLE` (in default config template)

For agents without a system prompt injection flag (Gemini, Aider, OpenCode), add a note to your project's `AGENTS.md` instructing the agent to read the `$YAAS_PREAMBLE` environment variable.

To disable preamble injection, set `preamble = false` in your config and remove `--append-system-prompt $YAAS_PREAMBLE` (or equivalent) from your tool commands.

### Argument Pass-Through

YAAS options must come first. Any unrecognized options are passed through to the underlying tool.

```bash
# YAAS options first, then tool options
yaas claude --ssh-agent --git-config -p "Fix the bug"
#           ^^^^^^^^^^^ ^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^
#           yaas options             passed to claude
```

### Ad-Hoc Mounts and Environment Variables

You can pass extra mounts and environment variables directly from the CLI without editing config files:

```bash
# Mount a directory into the container
yaas claude -v ~/datasets:/data

# Set or forward environment variables
yaas claude -e MY_VAR=hello -e SECRET_KEY

# Combine both
yaas run -v /tmp/input:/input:ro -e API_URL=http://localhost:8080 -- python process.py
```

`-e KEY=VALUE` sets a hardcoded value; `-e KEY` forwards the variable from the host (same as `KEY = true` in config).

### No-Project Mode

By default, YAAS mounts your current project directory into the container. Use `--no-project` to skip this and start in the container's home directory instead:

```bash
# Interactive shell without any project mount
yaas shell --no-project

# Run a tool without a project
yaas claude --no-project
```

This can also be set per-tool in config (useful for tools that don't need a project context):

```toml
[tools.my-hub]
command = ["my-tool", "hub"]
mount_project = false
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
yaas mise-upgrade

# Reset installed tools and cache (tools reinstall on next run)
yaas cleanup volumes

# Remove orphaned clone volumes
yaas cleanup clones
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
yaas claude --clone https://github.com/user/repo --network none

# Run a command in the cloned repo
yaas run --clone https://github.com/user/repo -- make test
```

This is useful for:
- Quickly exploring unfamiliar codebases with AI assistance
- Running AI agents on repos you don't want to clone permanently
- Exploring untrusted code without downloading it onto the host filesystem

If a container exits unexpectedly and leaves orphaned volumes, clean them up with:

```bash
yaas cleanup clones
```

## Configuration

YAAS uses a layered configuration system with two config files and per-tool overrides:

| Source | Location | Scope |
|--------|----------|-------|
| Global config | `~/.config/yaas/config.toml` | All projects |
| Project config | `.yaas.toml` in project root | Current project |

Both files share the same format and can contain both top-level settings and `[tools.*]` sections. Values merge with later sources taking precedence:

1. **Global config** — defaults for all projects
2. **Project config** — overrides global settings for this project
3. **Per-tool overrides** — `[tools.*]` sections override default settings when running that tool
4. **CLI flags** — `--runtime`, `--network`, `--memory`, etc. override everything

Tool definitions also merge field-by-field: if the global config defines `[tools.claude]` with `mounts` and `yolo_flags`, and the project `.yaas.toml` only sets `[tools.claude] network_mode = "none"`, the mounts and yolo_flags are preserved.

### All Options

```toml
# Container runtime (auto-detected if omitted)
# "podman", "podman-krun" (libkrun MicroVM), or "docker"
runtime = "podman"

# Feature flags
ssh_agent = true           # Forward SSH agent socket
git_config = true          # Mount .gitconfig and .config/git
podman = false             # Enable rootless Podman inside container (DinD)
podman_docker_socket = false  # Start Podman socket (Docker-compatible API)
devices = []              # Pass through host devices (e.g., ["/dev/fuse"])
clipboard = false          # Enable clipboard access for image pasting

# Isolation
network_mode = "bridge"    # "host", "bridge" (default), or "none"
mount_project = true       # Set to false to skip project directory mount
readonly_project = false   # Mount project directory as read-only
pid_mode = "host"          # PID namespace: "host" or isolated (default, omit)

# Auto-update behavior
auto_pull_image = true     # Pull container image on startup
auto_upgrade_tools = true  # Run mise upgrade on startup

# Resource limits
[resources]
memory = "8g"              # Memory limit (e.g., "4g", "512m")
memory_swap = "8g"         # Swap limit (set equal to memory to disable swap)
cpus = 4.0                 # CPU limit
pids_limit = 1000          # Maximum number of processes (prevents fork bombs)

# Global mounts — always applied, in "source:target:mode" format
mounts = [
    "~/datasets:/data:ro",
    "/var/log/app:/logs"
]

# Global environment variables — always forwarded
# true = forward from host, "value" = hardcoded
[env]
GITHUB_TOKEN = true
MY_VAR = "value"
```

### Tool Configuration

Each tool becomes a CLI command (`yaas claude`, `yaas codex`, etc.) and can declare its own `command`, `yolo_flags`, `mounts`, and `env`. Tool-specific mounts and env are **only applied when running that tool**, not for `yaas run` or `yaas shell`.

```toml
[tools.claude]
command = ["claude", "--append-system-prompt", "$YAAS_PREAMBLE"]
yolo_flags = ["--dangerously-skip-permissions"]  # Appended in YOLO mode
mounts = [".claude", ".claude.json", ".claude/ide:ro"]
env = { ANTHROPIC_API_KEY = true }

[tools.codex]
command = ["codex", "-c", "developer_instructions=$YAAS_PREAMBLE"]
yolo_flags = ["--dangerously-bypass-approvals-and-sandbox"]
mounts = [".codex"]
env = { OPENAI_API_KEY = true }
```

**Mount format:**
- `.claude` → mounts `~/.claude` to the same path in the sandbox (read-write)
- `.claude:ro` → same but read-only
- `~/a:/data:ro` → explicit `source:destination:mode`

**Env format:**
- `KEY = true` → forward `KEY` from the host environment (skipped if unset)
- `KEY = "value"` → set `KEY` to a hardcoded value

**Variable expansion:** The entrypoint expands all `$ENV_VAR` references in command arguments via `envsubst`, so any environment variable can be referenced in tool commands (e.g. `$YAAS_PREAMBLE`).

### Per-Tool Setting Overrides

Tools can also override any container setting. This is useful when different tools need different isolation levels or runtimes:

```toml
[tools.claude]
command = ["claude", "--append-system-prompt", "$YAAS_PREAMBLE"]
yolo_flags = ["--dangerously-skip-permissions"]
mounts = [".claude", ".claude.json", ".claude/ide:ro"]
env = { ANTHROPIC_API_KEY = true }

# Container setting overrides for this tool
runtime = "podman-krun"       # Use MicroVM isolation
ssh_agent = true
git_config = true
network_mode = "host"
readonly_project = true
pid_mode = "host"
mount_project = false

# Resource overrides (field-level merge with global)
[tools.claude.resources]
memory = "16g"
cpus = 4.0

# Security overrides (field-level merge with global)
[tools.claude.security]
capabilities = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "NET_BIND_SERVICE", "SETGID", "SETUID", "NET_RAW"]
```

Resource and security overrides merge at the field level — only the fields you specify are overridden, the rest inherit from the global/project config.

## Mise Integration

YAAS uses [mise](https://mise.jdx.dev/) to manage tools inside the container. This means you can install, upgrade, or remove tools without rebuilding the container image. The runtime image also includes [Nix](https://nixos.org/) and the [mise-nix](https://github.com/jbadeau/mise-nix) plugin, giving you access to 100,000+ packages from nixpkgs.

### How It Works

On first run, YAAS creates a default mise configuration at `~/.config/yaas/mise.toml`. Tool installations are stored in named volumes (`yaas-home` and `yaas-nix`), so they persist across sessions.

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

# Nix packages (100k+ available, see https://www.nixhub.io/)
"nix:php" = "latest"
"nix:shellcheck" = "latest"
"nix:htop" = "latest"

# AI tools
"npm:@anthropic-ai/claude-code" = "latest"
"aqua:anomalyco/opencode" = "latest"
# "npm:@openai/codex" = "latest"
# "npm:@google/gemini-cli" = "latest"
```

### Managing Tools

```bash
# Manually upgrade all tools
yaas mise-upgrade

# Reset all tools (reinstalls on next run)
yaas cleanup volumes
```

## How It Works

### UID Passthrough (Linux)

Traditional container sandboxes run processes as root or a mismatched UID, which causes file permission problems when the container writes to mounted directories. YAAS handles this differently depending on the runtime:

**Podman (rootless) / podman-krun:** The container process runs as "root" (UID 0) inside a user namespace, but this is **not real root** — the kernel maps container UID 0 to your host UID (e.g. 1000). The process has no access to the host's real root account and cannot escalate beyond your user's privileges. Files created in the container appear owned by your host user — no chown needed.

```
Host (UID 1000)                   Container (user namespace)
──────────────────────────────────────────────────────────────
Host UID 1000 ════════════════════> Container UID 0 (root)
~/projects/myapp ─────────────────> ~/projects/myapp (rw)
                                    ↳ Created as UID 0 inside
                                    ↳ Appears as UID 1000 on host ✓
```

**Docker (rootful):** The container starts as real root for setup, then `setpriv` drops to your host UID/GID at exec time. Volumes are chowned to your UID on first run.

```
Host (UID 1000)                   Container (no user namespace)
──────────────────────────────────────────────────────────────
YAAS_HOST_UID=1000 ───────────────> Entrypoint: chown volumes to 1000
                                    setpriv drops to UID 1000 at exec
~/projects/myapp ─────────────────> ~/projects/myapp (rw)
                                    ↳ Created as UID 1000 ✓
```

**Docker (rootless):** Same as Podman — user namespace maps UID 0 to host UID. No privilege drop needed.

**Docker (macOS):** Docker Desktop runs containers inside a Linux VM. File ownership is handled transparently by the file sharing layer (VirtioFS/gRPC-FUSE) — files created in containers appear owned by your macOS user regardless of the container UID. No UID passthrough or user namespace setup is needed.

In all cases, config files like `.gitconfig` and `.claude` can be mounted directly instead of copied, and files created inside the container have correct ownership on the host. Since the container's `/etc/passwd` is not bind-mounted from the host, you can freely `dnf install` packages that need to create system users.

For AI tools like Claude Code that check whether they're running as root, YAAS sets `IS_SANDBOX=1` in the tool's environment, which signals that root execution inside a sandbox is intentional.

### Persistent Volumes

YAAS uses named volumes to persist data across container sessions:

- `yaas-home` persists the entire home directory (`/home`), including mise tool installations, cache, shell history, and tool-specific configs
- `yaas-nix` stores the Nix store and database (`/nix`)

This is why tools installed via mise don't need to be reinstalled every time you start a new container. Running `yaas cleanup volumes` deletes these volumes, which will trigger a fresh tool installation on the next run.

### Runtime Options

YAAS supports multiple container runtimes:

| Runtime | Isolation | Notes |
|---------|-----------|-------|
| `podman` | Rootless container (default) | Best compatibility, user namespace support |
| `podman-krun` | libkrun MicroVM (KVM) | **Experimental.** Hardware-level isolation via lightweight VMs |
| `docker` | Docker container | Fallback, uses sudo if needed |

Set the runtime globally (`runtime = "podman-krun"`) or per-tool in config.

#### podman-krun (MicroVM) — Experimental

Uses [libkrun](https://github.com/containers/libkrun) to run containers inside lightweight KVM virtual machines. Requires the `crun-krun` package (provides the `krun` binary). This runtime is experimental — it works for daily use but has rough edges (see known limitations below).

**What works differently from regular Podman:**
- `sudo` and `apt install` work natively — no workarounds needed
- File ownership on the host is correct (same as regular Podman)
- YOLO mode works as expected
- `--network host` is not supported — YAAS automatically falls back to bridge. Use port publishing (`-p`) to expose services
- Clipboard, SSH agent, and container socket passthrough are automatically disabled (virtiofs can't forward host Unix sockets into the VM)
- lxcfs is automatically disabled (not needed — the VM has its own `/proc`)

**Known limitations:**
- **vsock errors on Linux 6.12+.** libkrun may print `BufDescTooSmall` errors during network-heavy operations. Cosmetic for most workloads but may cause hangs for large transfers. [Upstream fix in progress.](https://github.com/containers/libkrun/issues/535)
- Nix may show a cosmetic "no Internet access" warning on startup. Network connectivity works fine — YAAS configures Nix to ignore this check.

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

- **Tool mounts** (`mounts` in `[tools.*]`): Mounts tool-specific config dirs like `.claude`, `.codex`, `.gemini`. These may contain conversation history, cached credentials, or API keys. Only applied for the active tool.
- **Git config** (`git_config`): Mounts `.gitconfig` which may include credentials or credential helpers.
- **SSH agent** (`ssh_agent`): Forwards your SSH agent socket. The agent can use your SSH keys to authenticate to remote servers.
- **Podman DinD** (`podman`): Enables rootless Podman inside the container by adding `SYS_ADMIN` capability and `/dev/fuse` device. For Docker-outside-Docker, mount the host socket manually via `mounts = ["/var/run/docker.sock"]` — this gives root-equivalent access to your system.
- **API keys** (`env` in `[tools.*]`): Keys like `ANTHROPIC_API_KEY` are forwarded only for the specific tool that declares them. No keys are forwarded for `yaas run` or `yaas shell` unless declared in the global `[env]`.

The sandbox prevents the agent from accessing arbitrary files on your system, but anything you mount is fully accessible. If you're running untrusted code, consider disabling these options.

## License

MIT
