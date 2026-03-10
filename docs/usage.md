# Usage

## AI Tool Shortcuts

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

## Sandbox Awareness

AI agents running inside containers see a partial slice of the system — some paths may be missing, tools may not be pre-installed, and things may not work as expected compared to a full host environment. They may also pick up misleading information from mounted config files that reference host-specific paths or tools. Without context, agents can get confused and waste time debugging container artifacts. YAAS sets a `YAAS_PREAMBLE` environment variable that tells the agent it's in a sandbox and provides details about the environment — resource limits, network mode, mounted paths, and how to install tools. Agents that support system prompt injection receive this automatically:

- **Claude Code** uses `--append-system-prompt $YAAS_PREAMBLE` (configured by default)
- **Codex** uses `-c developer_instructions=$YAAS_PREAMBLE` (in default config template)

For agents without a system prompt injection flag (Gemini, Aider, OpenCode), add a note to your project's `AGENTS.md` instructing the agent to read the `$YAAS_PREAMBLE` environment variable.

To disable preamble injection, set `preamble = false` in your config and remove `--append-system-prompt $YAAS_PREAMBLE` (or equivalent) from your tool commands.

## Argument Pass-Through

YAAS options must come first. Any unrecognized options are passed through to the underlying tool.

```bash
# YAAS options first, then tool options
yaas claude --ssh-agent --git-config -p "Fix the bug"
#           ^^^^^^^^^^^ ^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^
#           yaas options             passed to claude
```

## Ad-Hoc Mounts and Environment Variables

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

## No-Project Mode

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

## General Commands

```bash
# Run any command in sandbox
yaas run -- make build

# Start an interactive shell
yaas shell
```

## Utility Commands

```bash
# Show current configuration
yaas config

# Show resolved container spec for a tool (after all config merging)
yaas config claude

# Manually pull the latest container image
yaas pull-image

# Manually upgrade mise-managed tools
yaas mise-upgrade

# Reset installed tools and cache (tools reinstall on next run)
yaas cleanup volumes
```

## Git Worktrees

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

## Persistent Boxes

Boxes are long-lived containers that persist across sessions. Unlike `yaas run` (which creates ephemeral containers), boxes stay around until you explicitly remove them.

```bash
# Create a box with a named spec from config
yaas box create dev shell

# Create an ad-hoc box with CLI flags
yaas box create scratch --ssh-agent --memory 8g

# Enter a running box (interactive shell)
yaas box exec dev bash

# Execute a command in a running box
yaas box exec dev -- make test

# Lifecycle management
yaas box stop dev
yaas box start dev
yaas box remove dev

# List all YAAS-managed boxes
yaas box list

# Show detailed info about a running box
yaas box info dev

# Show resolved container spec for a box (before creating)
yaas box config shell
yaas box config shell --ssh-agent --memory 16g
```

Box specs are defined in config with `[box.*]` sections. They support the same settings as tools (mounts, env, resources, security, etc.) plus box-specific fields:

```toml
[box.shell]
ssh_agent = true
git_config = true
clipboard = true

[box.hardened]
base = "none"
network_mode = "none"
```

By default, boxes do **not** mount the project directory (unlike `yaas run`). Set `mount_project = true` in the box spec or use `--mount` to add directories.

### Podman Quadlet Generation

The `--quadlet` flag generates a [Podman quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) `.container` file instead of creating the container directly. This lets you manage the box as a systemd service:

```bash
# Generate and save the quadlet file
yaas box create dev shell --quadlet > ~/.config/containers/systemd/yaas-box-dev.container

# Activate the service
systemctl --user daemon-reload
systemctl --user start yaas-box-dev

# Enter the quadlet-managed box
yaas box exec dev bash

# Manage via systemctl
systemctl --user stop yaas-box-dev
systemctl --user restart yaas-box-dev
```

Quadlet-managed containers are not tracked by YAAS (`yaas box list` won't show them) — systemd owns their lifecycle.

## Clipboard and Image Pasting

The `--clipboard` flag enables clipboard access for pasting images into AI agents. This works by mounting display server sockets into the container.

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
