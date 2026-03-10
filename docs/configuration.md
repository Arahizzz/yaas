# Configuration

YAAS uses a layered configuration system with two config files and per-tool overrides:

| Source | Location | Scope |
|--------|----------|-------|
| Global config | `~/.config/yaas/config.toml` | All projects |
| Project config | `.yaas.toml` in project root | Current project |

Both files share the same format and can contain both top-level settings and `[tools.*]` sections. Settings are applied in order:

1. **Global config** — defaults for all projects
2. **Project config** — extends or overrides global settings for this project
3. **Per-tool overrides** — `[tools.*]` sections override default settings when running that tool
4. **CLI flags** — `--runtime`, `--network`, `--memory`, etc. override everything

**How fields merge between global and project config:**

- **Scalar settings** (bools, strings like `ssh_agent`, `network_mode`): project **replaces** global.
- **List fields** (`mounts`, `ports`, `devices`): project **extends** global — both lists are concatenated.
- **Dict fields** (`env`): project **merges** into global — project keys win on conflict, global-only keys are preserved.
- **Nested objects** (`resources`, `security`): field-level merge — only specified sub-fields are overridden.

The same rules apply to `[tools.*]` sections: if both global and project config define `[tools.claude]`, their `mounts` are concatenated, `env` dicts are merged, and scalar fields like `network_mode` are replaced. Fields not mentioned in the project config are preserved from the global config. `command` and `yolo_flags` are exceptions — they are always replaced, not merged.

## All Options

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

## Tool Configuration

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

## Per-Tool Setting Overrides

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

## Box Configuration

Box specs are defined with `[box.*]` sections and support the same container settings as tools (mounts, env, resources, security, network, etc.), plus box-specific fields:

```toml
[box.shell]
ssh_agent = true
git_config = true
clipboard = true
shell = ["zsh"]                    # Shell for `yaas box enter` (default: bash)
# command = ["sleep", "infinity"]     # Default, override for custom init process

[box.hardened]
base = "none"                      # Skip global config inheritance
network_mode = "none"
mount_project = true

[box.hardened.resources]
memory = "4g"
pids_limit = 500
```

The `base` field controls config inheritance:
- `"default"` — inherits from global/project config (default)
- `"minimal"` — starts from a hardcoded baseline, ignoring global config
- `"none"` — bare container with no shared volumes or optional mounts

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
