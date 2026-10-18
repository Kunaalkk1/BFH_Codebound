# Linux setup

Both the independent host and official-kit demo use native Python launchers. No PowerShell, Windows DPAPI or copied Windows environment is required. The initial target is amd64 Linux with Docker Engine; Ubuntu 24.04 is the CI target.

## Prerequisites

Use Python 3.11+, Git, Docker Engine and its Compose plugin 2.24.4+. On Ubuntu 24.04:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip git
```

Install Docker using [Docker's Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/) or [your distribution's guide](https://docs.docker.com/engine/install/). Verify `python3 --version`, `docker info` and `docker compose version` as your normal user. Follow [Docker's post-installation instructions](https://docs.docker.com/engine/install/linux-postinstall/) for user access; Docker group membership grants privileged Docker access. The launcher does not change groups, use sudo, reset volumes or modify firewall settings. Rootless Docker and ARM emulation are not the validated demo target.

## Your own host

```bash
git clone --depth 1 --branch dev_sanchit https://github.com/Kunaalkk1/BFH_Codebound.git
cd BFH_Codebound
python3 launch.py
```

Create your administrator at http://localhost:3200/login, then configure connectors and an MCP server. Codebound scripts are at http://127.0.0.1:8002. Host startup needs neither Node nor Ollama.

```bash
python3 launch.py --status
python3 launch.py --stop
python3 launch.py --test
```

Linux uses `.venv/bin/python`, Unix file locking, detached API processes and a private `.local/host` directory. Stop preserves users, scripts, encryption keys and volumes. Do not run the entire launcher with sudo or copy another machine's `.local` or virtual environments.

## Official demo

Install Node.js 20+ and native [Ollama for Linux](https://docs.ollama.com/linux). Verify `node --version` and `ollama --version`. Then:

```bash
python3 launch.py demo --setup
python3 launch.py demo
python3 launch.py demo --status
python3 launch.py demo --benchmark
python3 launch.py demo --stop
```

Setup fetches the tested official kit, installs backend dependencies, pulls the configured model and provisions isolated mock services and workspace users. `OLLAMA_MODEL` selects a local model; the default is `qwen2.5-coder:3b`. For a custom CLI path, export `CODEBOUND_OLLAMA_EXE=/full/path/to/ollama`. Existing Ollama servers are reused. Setup refuses to overwrite a populated database or mock credentials when matching setup state is missing.

Demo addresses are http://localhost:3100, MCP :4200 and API :8001, with mock ports 14101–14104/14180/14181/14199. Alice/Bob/Carol exist only in this explicit mode. Native Docker uses a `host-gateway` route; the API listens on loopback and that specific private Docker interface, never all LAN interfaces. A custom firewall must permit this Docker-to-host route.

Official mandatory checks after startup:

```bash
export CODEBOUND_STATE_DIR="$PWD/.local/kit/state"
export CODEBOUND_MOCKS_ADMIN=http://127.0.0.1:14180
export CODEBOUND_OUTSIDE_ADMIN=http://127.0.0.1:14181
node external/codebound-kit/harness/cli.mjs check --team .local/kit/team.json --only must
```

### Recovering a demo that fails to start

Update this checkout and rerun `python3 launch.py demo` (or `python3 launch.py demo --setup` if setup never completed). The Linux launcher backs up and repairs the known retired AnythingMCP image digest while retaining secrets and operator-selected images. Mock image builds now set readable source permissions explicitly, including for existing checkouts created with `umask 077`. Existing accounts and Docker volumes are retained.

## Claude JSON

Install Claude Desktop following [Anthropic's platform instructions](https://support.claude.com/en/articles/10065433-install-claude-desktop). For your own host and scoped per-user MCP key:

```bash
python3 configure_claude.py --install
```

The Linux config path uses `$XDG_CONFIG_HOME/Claude/claude_desktop_config.json`, falling back to `~/.config/Claude/claude_desktop_config.json`. Use `--config /absolute/path/to/config.json` for another installation. Existing servers/preferences are preserved; reopen Claude yourself.

For the running official demo:

```bash
python3 configure_claude.py --demo --install
```

This explicitly selects demo Alice and exposes only the five reviewed tools under `codebound-kit-alpha`. Raw tools and Payroll remain hidden. Omit `--install` to generate JSON only, including for another compatible stdio MCP client. Keys are stored in mode-600 files under a mode-700 directory, not in generated JSON. No Windows encryption package is imported on Linux.

## Verification scope

Linux-container tests exercise the default suite, Unix credentials and native launch paths. Ubuntu CI is configured for Python 3.11/3.13, real Docker isolation and native demo provisioning/start/reuse/stop plus official MUST checks. Its native demo job deliberately skips Ollama inference; it must not be read as a generation test. The Claude Linux desktop UI and every distribution/firewall combination are outside those tests.
