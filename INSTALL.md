# Installing Hercules Agent

This is the complete installation guide for this repository. Every command in
it is taken from — and checked against — the files in this repo
(`pyproject.toml`, `setup-hercules.sh`, `scripts/install.sh`,
`scripts/install.ps1`, `docker-compose.yml`, `flake.nix`,
`constraints-termux.txt`). The from-source path in the Quick Start was
executed end-to-end before this guide was written.

**Contents**

- [Requirements](#requirements)
- [Quick Start — install from this repository (verified)](#quick-start--install-from-this-repository-verified)
- [Option A: guided setup script](#option-a-guided-setup-script-setup-herculessh)
- [Option B: manual install with uv](#option-b-manual-install-with-uv)
- [Option C: plain venv + pip (no uv)](#option-c-plain-venv--pip-no-uv)
- [Option D: hosted one-line installers](#option-d-hosted-one-line-installers)
- [Android / Termux](#android--termux)
- [Windows (native)](#windows-native)
- [Docker / Docker Compose](#docker--docker-compose)
- [Nix / NixOS](#nix--nixos)
- [Homebrew](#homebrew)
- [Optional dependency extras](#optional-dependency-extras)
- [After installation](#after-installation)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

---

## Requirements

| Requirement | Version | Notes |
| --- | --- | --- |
| Python | **>= 3.11, < 3.14** | The upper bound is enforced by `pyproject.toml` (`requires-python`): some compiled dependencies have no Python 3.14 wheels yet, and 3.14 would force source builds that fail. `uv` can download 3.11 for you — no system Python needed. |
| Git | any recent | Needed to clone the repo; also used by the agent at runtime. |
| [uv](https://docs.astral.sh/uv/) | any recent | Recommended installer. `setup-hercules.sh` installs it automatically if missing. Not used on Termux (stdlib `venv` + `pip` there). |
| Node.js | 22 (optional) | Only for browser automation and the WhatsApp bridge. |
| ripgrep | optional | Faster file search; the agent falls back to `grep` without it. |
| ffmpeg | optional | Audio conversion for voice/TTS features. |

Supported platforms: Linux, macOS, WSL2, native Windows (PowerShell), and
Android via Termux. Nix/NixOS is best-effort (Tier 2 — see
`website/docs/getting-started/nix-setup.md`).

---

## Quick Start — install from this repository (verified)

This is the authoritative path for this repo, and the exact sequence below was
run and verified (install completes hash-verified, and `hercules --version` /
`hercules --help` work) before this document was committed:

```bash
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-

# Hash-verified install of the curated "all" extra from uv.lock.
# UV_PROJECT_ENVIRONMENT controls where the venv is created.
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv sync --extra all --locked

# Verify:
venv/bin/hercules --version
venv/bin/hercules doctor
```

Why `uv sync --locked` and not plain `pip install`: `uv.lock` records a SHA256
hash for every transitive dependency, so a compromised or replaced package on
PyPI is **rejected** instead of installed. This is the only install path that
protects against transitive supply-chain attacks (direct dependencies are
exact-pinned in `pyproject.toml`, but plain `pip`/`uv pip install` re-resolves
transitives fresh from PyPI).

Use `--extra all` (the curated set), **not** `--all-extras` — the latter pulls
every optional backend including ones that need native toolchains (e.g. the
`matrix` extra's `python-olm` has no build path on Windows/macOS without
`make`).

---

## Option A: guided setup script (`setup-hercules.sh`)

For a guided version of the Quick Start — with uv auto-install, Python 3.11
provisioning, `.env` creation, PATH setup, and skill seeding — run the setup
script from a clone:

```bash
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-
./setup-hercules.sh
```

What it does (in order):

1. Detects desktop/server vs Termux and picks the right toolchain (uv vs stdlib `venv` + `pip`).
2. Installs `uv` if missing, and Python 3.11 via `uv python install` if missing.
3. Creates `venv/` in the repo and installs dependencies — preferring the
   hash-verified `uv sync --extra all --locked` path, with documented pip
   fallbacks if the lockfile sync fails.
4. Offers to install ripgrep (apt/dnf/brew/cargo/pkg, whichever is available).
5. Creates `.env` from `.env.example` (chmod 600 — it will hold API keys).
6. Symlinks `hercules` into `~/.local/bin` (or `$PREFIX/bin` on Termux) and
   ensures that directory is on your `PATH`.
7. Seeds bundled skills into `~/.hercules/skills/`.
8. Offers to run the interactive setup wizard (`hercules setup`).

Afterwards:

```bash
source ~/.bashrc   # or ~/.zshrc — reload PATH
hercules setup     # configure provider + API keys (if you skipped the wizard)
hercules           # start chatting
```

---

## Option B: manual install with uv

Full manual control, same commands the setup script uses under the hood:

```bash
# 1. Install uv (skip if you have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-

# 3a. Hash-verified (preferred — uses uv.lock):
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv sync --extra all --locked

# 3b. Or resolve fresh from PyPI (NOT hash-verified):
uv venv venv --python 3.11
VIRTUAL_ENV="$PWD/venv" uv pip install -e ".[all]"

# 4. Make the command available
mkdir -p ~/.local/bin
ln -sf "$PWD/venv/bin/hercules" ~/.local/bin/hercules
```

For development (tests, linters, debugger), add the `dev` extra:

```bash
VIRTUAL_ENV="$PWD/venv" uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

> **Contributor note (from the README):** if you're working from a throwaway
> clone, create the venv **outside** the source tree (e.g.
> `uv venv ~/.hercules/venvs/hercules-dev --python 3.11`). A venv inside the
> directory the agent operates on can be wiped by a relative-path command the
> agent runs against its own checkout, destroying the running runtime
> mid-session.

---

## Option C: plain venv + pip (no uv)

Works anywhere Python 3.11–3.13 is installed, with the caveat that transitives
are not hash-verified:

```bash
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-
python3 -m venv venv
venv/bin/pip install --upgrade pip setuptools wheel
venv/bin/pip install -e ".[all]"     # or just -e "." for the lean core
```

If the `all` extra fails to resolve (e.g. a quarantined upstream release), the
core install (`pip install -e "."`) still gives you a working agent — optional
backends lazy-install at first use via `tools/lazy_deps.py`.

---

## Option D: one-line installers

The installer scripts live in this repository (`scripts/install.sh`,
`scripts/install.ps1`) and clone **this** repository. They are served straight
from GitHub — no third-party hosting involved:

```bash
# Linux / macOS / WSL2 / Termux
curl -fsSL https://raw.githubusercontent.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-/main/scripts/install.sh | bash
```

```powershell
# Windows (native, PowerShell)
iex (irm https://raw.githubusercontent.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-/main/scripts/install.ps1)
```

The scripts do more than the Quick Start: they also install Node.js, ripgrep,
ffmpeg, and (on Windows) a portable Git Bash, and set up the managed
`~/.hercules/hercules-agent` layout that `hercules update` expects.

The installer supports useful flags (parsed by `scripts/install.sh`):

```bash
curl -fsSL https://raw.githubusercontent.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-/main/scripts/install.sh | bash -s -- --skip-browser   # no Playwright/Chromium
curl -fsSL https://raw.githubusercontent.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-/main/scripts/install.sh | bash -s -- --no-venv --skip-setup
curl -fsSL https://raw.githubusercontent.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-/main/scripts/install.sh | bash -s -- --branch <name>  # install a specific branch
```

Install layout used by the installer:

| Mode | Code | `hercules` command | Data |
| --- | --- | --- | --- |
| Per-user | `~/.hercules/hercules-agent/` | `~/.local/bin/hercules` (symlink) | `~/.hercules/` |
| Root (FHS) | `/usr/local/lib/hercules-agent/` | `/usr/local/bin/hercules` | `$HERCULES_HOME` (default `/root/.hercules/`) |
| Windows | `%LOCALAPPDATA%\hercules\hercules-agent\` | `%LOCALAPPDATA%\hercules\bin` | `%LOCALAPPDATA%\hercules\` |

---

## Android / Termux

Termux uses Python's stdlib `venv` + `pip` (not uv), and a curated `[termux]`
extra — the full `[all]` extra currently pulls Android-incompatible voice
dependencies. The pins in `constraints-termux.txt` keep the tested Android
path stable:

```bash
pkg install python git ripgrep
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-
./setup-hercules.sh          # detects Termux and does all of the below
```

Or manually:

```bash
python -m venv venv
venv/bin/pip install --upgrade pip setuptools wheel
venv/bin/pip install -e ".[termux]" -c constraints-termux.txt
ln -sf "$PWD/venv/bin/hercules" "$PREFIX/bin/hercules"
```

A best-effort `[termux-all]` extra also exists (adds google/homeassistant/
sms/web on top of `[termux]`).

---

## Windows (native)

Native Windows is supported without WSL. The PowerShell installer
(`scripts/install.ps1`, hosted as shown in [Option D](#option-d-hosted-one-line-installers))
handles uv, Python 3.11, Node.js, ripgrep, ffmpeg, and a portable Git Bash
(MinGit under `%LOCALAPPDATA%\hercules\git` — no admin rights required; an
existing Git install is detected and used instead).

From CMD, `scripts/install.cmd` is a thin wrapper that launches the same
PowerShell installer.

To install **this repo's** code on Windows manually, the uv path works the
same as on Linux/macOS (run from PowerShell or Git Bash):

```powershell
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-
$env:UV_PROJECT_ENVIRONMENT="$PWD\venv"; uv sync --extra all --locked
venv\Scripts\hercules --version
```

If Windows Defender or another antivirus quarantines `uv.exe`, that is a known
false positive on Astral's unsigned Rust binary — see the "Troubleshooting"
section of `README.md` for the attestation-based verification steps and
whitelisting guidance.

WSL2 users: follow the Linux instructions instead; the install lands under
`~/.hercules` as on Linux.

---

## Docker / Docker Compose

The repo ships a `Dockerfile` (Debian 13 base, s6-overlay supervision, Node 22,
Playwright) and a `docker-compose.yml` that runs the messaging gateway with
`~/.hercules` bind-mounted for persistent data:

```bash
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-
HERCULES_UID=$(id -u) HERCULES_GID=$(id -g) docker compose up -d
```

`HERCULES_UID`/`HERCULES_GID` map the container's internal user to the host
user that owns `~/.hercules`, so files created inside the container stay
readable on the host.

Security notes from `docker-compose.yml` worth repeating:

- The dashboard binds to `127.0.0.1` by default and stores API keys. Do not
  expose it on a LAN without auth — use an SSH tunnel or an authenticating
  reverse proxy; never `--insecure --host 0.0.0.0`.
- Keep `/init` (s6-overlay) as PID 1 if you override the entrypoint —
  bypassing it skips the container's init/supervision setup and the gateway
  will not work correctly.
- The gateway API server stays off unless you explicitly set both
  `API_SERVER_HOST` and `API_SERVER_KEY`.

A `docker-compose.windows.yml` variant exists for Windows hosts.

---

## Nix / NixOS

Best-effort (Tier 2) support via the flake in this repo (`flake.nix`, built
with uv2nix; systems: `x86_64-linux`, `aarch64-linux`, `aarch64-darwin`):

```bash
# Run without installing
nix run github:mintoriakamoto/Hercules-Agent-Hermes-Brother- -- setup
nix run github:mintoriakamoto/Hercules-Agent-Hermes-Brother- -- --tui

# Install into your profile
nix profile install github:mintoriakamoto/Hercules-Agent-Hermes-Brother-

# Development shell from a local clone
git clone https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-.git
cd Hercules-Agent-Hermes-Brother-
nix develop
```

The default package bundles all portable optional integrations (~700 MB
closure); smaller variants (e.g. `#messaging`, ~33 MB extra) are exposed as
separate flake outputs. A declarative NixOS module (native and container
modes) is in `nix/nixosModules.nix`. Full guide:
`website/docs/getting-started/nix-setup.md`.

---

## Homebrew

`packaging/homebrew/hercules-agent.rb` is a **formula template** for use as a
tap or homebrew-core starting point — it is not a published, ready-to-install
formula. As committed, its `url` points at an upstream GitHub release asset
and its `sha256` is a `<replace-with-release-asset-sha256>` placeholder, so
`brew install` of this file as-is will not work without filling those in. See
`packaging/homebrew/README.md` for the maintenance workflow.

---

## Optional dependency extras

Everything provider- or platform-specific is an optional extra
(`pip install -e ".[<extra>]"` / `uv sync --extra <extra>`). Most of these
also **lazy-install at first use** via `tools/lazy_deps.py`, so you rarely
need to pre-install them. From `pyproject.toml`:

| Extra | What it enables |
| --- | --- |
| `all` | Curated bundle: cron, cli, pty, mcp, homeassistant, sms, acp, google, web, youtube. Deliberately excludes lazy-installable backends so one bad PyPI release can't break fresh installs. |
| `anthropic` | Native Anthropic provider (not needed when going through OpenRouter). |
| `messaging` | Telegram, Discord, Slack (+voice, QR pairing). |
| `slack`, `matrix`, `dingtalk`, `feishu`, `wecom`, `teams`, `sms`, `homeassistant` | Individual messaging/platform adapters. `matrix` needs a native toolchain (`python-olm`). |
| `voice` | Local speech-to-text (faster-whisper, sounddevice, numpy). |
| `edge-tts`, `tts-premium`, `mistral` | TTS/STT backends (Edge TTS, ElevenLabs, Voxtral). |
| `exa`, `firecrawl`, `parallel-web` | Web-search backends. |
| `fal` | Image generation. |
| `modal`, `daytona` | Serverless terminal backends. |
| `bedrock`, `vertex`, `azure-identity` | AWS/GCP/Azure provider auth. |
| `honcho`, `hindsight`, `supermemory`, `mem0` | Memory providers. |
| `mcp`, `acp` | Model Context Protocol / Agent Client Protocol support. |
| `web` | `hercules dashboard` (localhost SPA + API). |
| `google`, `youtube` | Google Workspace skill deps; YouTube transcript skills. |
| `dev` | pytest, ruff, ty, debugpy — for contributors. |
| `termux`, `termux-all` | Curated Android bundles. |
| `cron`, `cli`, `pty`, `vision` | Back-compat aliases (their contents are now core deps or included elsewhere). |

Installed console commands (from `[project.scripts]`): `hercules` (main CLI),
`hercules-agent` (direct agent runner), `hercules-acp` (ACP adapter).

---

## After installation

```bash
hercules setup          # full interactive setup wizard (provider, keys, tools)
hercules model          # choose LLM provider and model
hercules tools          # configure which tools are enabled
hercules doctor         # diagnose configuration problems
hercules                # start chatting (TUI)
hercules gateway setup  # configure messaging platforms (Telegram, Discord, …)
hercules gateway install # install the gateway as a background service
hercules status         # check configuration
hercules cron list      # view scheduled jobs
```

Fastest provider setup — a single OpenRouter key covers hundreds of models:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' >> ~/.hercules/.env
hercules model
```

API keys live in `.env` (repo installs) or `~/.hercules/.env` — keep the file
`chmod 600`, which the setup scripts do for you.

---

## Updating

Hercules auto-detects how it was installed (pip, git installer, Homebrew, Nix)
and `hercules update` prints/uses the matching update path. For a from-source
clone of this repo:

```bash
git pull
UV_PROJECT_ENVIRONMENT="$PWD/venv" uv sync --extra all --locked
```

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `hercules: command not found` | Reload your shell (`source ~/.bashrc` / `~/.zshrc`) or check that `~/.local/bin` is on `PATH`. |
| `ModuleNotFoundError: No module named 'dotenv'` | You ran the repo's `./hercules` wrapper with system Python. Use the venv launcher (`venv/bin/hercules`) or the `~/.local/bin/hercules` symlink. |
| `uv sync` refuses Python 3.14 | Intentional — see [Requirements](#requirements). Use 3.11–3.13 (`uv python install 3.11`). |
| `[all]` extra fails to resolve | Install the core (`uv pip install -e "."`); optional backends lazy-install at first use. |
| API key not set | `hercules model`, or `hercules config set OPENROUTER_API_KEY <key>`. |
| Missing config after update | `hercules config check` then `hercules config migrate`. |
| Antivirus flags `uv.exe` (Windows) | Known false positive — verification steps in `README.md` → Troubleshooting. |

For anything else, `hercules doctor` reports exactly what's missing and how to
fix it. Full documentation lives under `website/docs/` in this repo (Docusaurus
site) — start with `website/docs/getting-started/quickstart.md`.
