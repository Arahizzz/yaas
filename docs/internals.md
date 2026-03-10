# How It Works

## UID Passthrough (Linux)

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

## Persistent Volumes

YAAS uses named volumes to persist data across container sessions:

- `yaas-home` persists the entire home directory (`/home`), including mise tool installations, cache, shell history, and tool-specific configs
- `yaas-nix` stores the Nix store and database (`/nix`)

This is why tools installed via mise don't need to be reinstalled every time you start a new container. Running `yaas cleanup volumes` deletes these volumes, which will trigger a fresh tool installation on the next run.

## Runtime Options

YAAS supports multiple container runtimes:

| Runtime | Isolation | Notes |
|---------|-----------|-------|
| `podman` | Rootless container (default) | Best compatibility, user namespace support |
| `podman-krun` | libkrun MicroVM (KVM) | **Experimental.** Hardware-level isolation via lightweight VMs |
| `docker` | Docker container | Fallback, uses sudo if needed |

Set the runtime globally (`runtime = "podman-krun"`) or per-tool in config.

### podman-krun (MicroVM) — Experimental

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

## Security Considerations

YAAS provides filesystem and resource isolation, but it intentionally mounts sensitive files to make AI agents useful. You should understand what you're exposing:

- **Tool mounts** (`mounts` in `[tools.*]`): Mounts tool-specific config dirs like `.claude`, `.codex`, `.gemini`. These may contain conversation history, cached credentials, or API keys. Only applied for the active tool.
- **Git config** (`git_config`): Mounts `.gitconfig` which may include credentials or credential helpers.
- **SSH agent** (`ssh_agent`): Forwards your SSH agent socket. The agent can use your SSH keys to authenticate to remote servers.
- **Podman DinD** (`podman`): Enables rootless Podman inside the container by adding `SYS_ADMIN` capability and `/dev/fuse` device. For Docker-outside-Docker, mount the host socket manually via `mounts = ["/var/run/docker.sock"]` — this gives root-equivalent access to your system.
- **API keys** (`env` in `[tools.*]`): Keys like `ANTHROPIC_API_KEY` are forwarded only for the specific tool that declares them. No keys are forwarded for `yaas run` or `yaas shell` unless declared in the global `[env]`.

The sandbox prevents the agent from accessing arbitrary files on your system, but anything you mount is fully accessible. If you're running untrusted code, consider disabling these options.
