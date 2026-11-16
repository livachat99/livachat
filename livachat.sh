#!/usr/bin/env bash
# LivaChat local service manager — install / start / stop / status
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs/test"   # test-instance log directory (isolated from manual dev instances at <repo>/logs)
TEST_CFG="$ROOT/configs/test"
DEV_CFG="$ROOT/configs/dev"
SRV_LOG="$LOG_DIR/livachat.log"
SRV_BOOT="$LOG_DIR/livachat.boot.log"
MCP_LOG="$LOG_DIR/lvdata_mcp.log"
MCP_BOOT="$LOG_DIR/lvdata_mcp.boot.log"
SRV_PID="$RUN_DIR/livachat.pid"
MCP_PID="$RUN_DIR/lvdata_mcp.pid"

# Optional LiveKit server (installed by `install --livekit`; see docs/livekit_install.md)
BIN_DIR="$ROOT/bin/livekit"
LIVEKIT_BIN="$BIN_DIR/livekit-server"
LIVEKIT_CONFIG="$BIN_DIR/livekit.yaml"
LIVEKIT_SAMPLE="$BIN_DIR/config-sample.yaml"
LIVEKIT_VERSION_FILE="$BIN_DIR/VERSION"
LIVEKIT_DEFAULT_VERSION="1.13.11"
LIVEKIT_KEY=""
LIVEKIT_SECRET=""
LIVEKIT_TMP_DIR=""
LIVEKIT_ENV_TMP=""
LIVEKIT_CONFIG_TMP=""

# Ports come from configs/test (resolve_ports); fall back to the defaults recorded in REPO.md if unreadable.
# SRV_SCHEME/SRV_HOST follow server.ssl.enabled/server.host via the service's own load_config()
# (docs/livachat_start.md §3.5/§3.8).
SRV_PORT=8080
MCP_PORT=8090
SRV_SCHEME=https
SRV_HOST=0.0.0.0

info() { printf '%s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
LivaChat local service manager (chat server + lvdata-mcp)

Usage: ./livachat.sh <command> [options]

  install   Install uv and Python 3.12, create .venv, editable-install services and dependencies
  start     Start both services in background and wait for health checks
  stop      Gracefully stop both services (SIGTERM, SIGKILL after 10s if still running)
  status    Show process and /healthz status; exit 0 when all running and healthy, otherwise 1

install options:
  --livekit                Install LiveKit Server into <repo>/bin/livekit
  --skip-base              With --livekit: skip the Python base install
  --livekit-version <ver>  LiveKit version (default: 1.13.11)
  --dry-run                Print planned actions; no network access, no writes
  -h, --help               Show this help
EOF
}

# ---------------------------------------------------------------- utilities

# Read server.ssl.enabled and server.host through the service's own loader so .env,
# ${ENV_VAR} placeholders, LIVACHAT__ overrides and Pydantic parsing all match the real server.
read_server_runtime() {
  (
    cd "$ROOT"
    LIVA_CHAT_CONFIG="$TEST_CFG/server.yaml" "$VENV/bin/python" - <<'PY'
from shared.config.loader import load_config

cfg = load_config()
print("ssl=" + ("true" if cfg.server.ssl.enabled else "false"))
print("host=" + cfg.server.host)
PY
  )
}

# Read ports from the actual config (guarantees consistency with configs/test rather than guessing)
# and resolve the health/display URL scheme and display host. Port reading is unchanged: a missing
# port key falls back to the defaults above. An unreadable venv/config or a load/validation failure
# is fatal: guessing http/https or the bind host would make status/start show the wrong endpoint
# (docs/livachat_start.md §3.5/§3.8).
resolve_ports() {
  local p runtime ssl host
  [ -x "$VENV/bin/python" ] \
    || die ".venv/bin/python not found; cannot resolve ports or server runtime—run ./livachat.sh install first"
  [ -f "$TEST_CFG/server.yaml" ] \
    || die "configs/test/server.yaml not found; cannot resolve server runtime—run ./livachat.sh install first"

  p="$(cd "$ROOT" && "$VENV/bin/python" -c 'import yaml;print(yaml.safe_load(open("configs/test/server.yaml"))["server"]["port"])' 2>/dev/null || true)"
  if [ -n "$p" ]; then SRV_PORT="$p"; fi
  p="$(cd "$ROOT" && "$VENV/bin/python" -c 'import yaml;print(yaml.safe_load(open("configs/test/lvdata_mcp.yaml"))["server"]["port"])' 2>/dev/null || true)"
  if [ -n "$p" ]; then MCP_PORT="$p"; fi

  if ! runtime="$(read_server_runtime 2>&1)"; then
    printf '%s\n' "$runtime" >&2
    die "failed to load $TEST_CFG/server.yaml; cannot determine server.ssl.enabled/server.host (run ./livachat.sh install or fix the config/.env)"
  fi
  ssl="$(printf '%s\n' "$runtime" | sed -n 's/^ssl=//p' | tail -n 1 | tr -d '[:space:]')"
  host="$(printf '%s\n' "$runtime" | sed -n 's/^host=//p' | tail -n 1 | tr -d '\r')"
  case "$ssl" in
    true)  SRV_SCHEME=https ;;
    false) SRV_SCHEME=http ;;
    *)
      die "cannot determine server.ssl.enabled from $TEST_CFG/server.yaml (got '$ssl'); run ./livachat.sh install or fix the config/.env"
      ;;
  esac
  [ -n "$host" ] || host=0.0.0.0
  SRV_HOST="$host"
}

# Port bind test (tries 0.0.0.0 and 127.0.0.1 separately with SO_REUSEADDR, matching actual
# startup semantics; covering both address families catches 127.0.0.1-only listeners that a
# 0.0.0.0-only check misses on macOS); a missing venv python counts as free
port_free() {
  [ -x "$VENV/bin/python" ] || return 0
  "$VENV/bin/python" -c '
import socket, sys
port = int(sys.argv[1])
for addr in ("0.0.0.0", "127.0.0.1"):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((addr, port))
    except OSError:
        raise SystemExit(1)
    finally:
        s.close()
' "$1" 2>/dev/null
}

# First free port: base, +100, +200… (step 100, at most 10 attempts)
pick_free_port() {
  local port="$1" i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if port_free "$port"; then
      printf '%s' "$port"
      return 0
    fi
    printf 'Port %s is in use, trying %s\n' "$port" "$((port + 100))" >&2
    port=$((port + 100))
  done
  warn "all candidates in use, falling back to ${port} (not verified free)"
  printf '%s' "$port"
}

# Read the port under the server section of a yaml file (for bff alignment; empty on failure)
server_port_of() {
  awk '/^server:/{f=1;next} f&&/^  port:/{gsub(/[^0-9]/,"",$2);print $2;exit}' "$1" 2>/dev/null || true
}

# Effective value of a required environment variable (shell wins—including set-but-empty;
# when unset, read repo-root .env: later lines override earlier ones, tolerates export prefix
# and paired quotes—a subset of the loader's setdefault semantics)
env_effective() {
  local name="$1" seen line
  eval "seen=\${$name+set}"
  if [ -n "$seen" ]; then
    eval "printf '%s' \"\$$name\""
    return 0
  fi
  [ -f "$ROOT/.env" ] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$name=" "$ROOT/.env" 2>/dev/null | tail -n 1 || true)"
  [ -n "$line" ] || return 0
  line="${line#*=}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  line="${line%$'\r'}"
  case "$line" in
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
  esac
  printf '%s' "$line"
}

# Whether a variable is defined at all (shell—even set-but-empty—or a line in repo-root .env).
# Only the start preflight uses this; scheme resolution must go through load_config() instead
# (docs/livachat_start.md §3.8).
env_defined() {
  local name="$1" seen
  eval "seen=\${$name+set}"
  [ -n "$seen" ] && return 0
  [ -f "$ROOT/.env" ] || return 1
  grep -Eq "^[[:space:]]*(export[[:space:]]+)?$name=" "$ROOT/.env" 2>/dev/null
}

# start preflight: check required items one by one (.env absent or required item empty → list and die)
# Basis: install_design.md §1.4; chat validation.py:30-33 (dev_token non-empty),
# lvdata validate_required (api_key non-empty). WORKSPACE_ID empty string allowed per §1.4 → warning only.
# WORKSPACE_ID / HERMES_AUTH_TOKEN / OPENCLAW_AUTH_TOKEN must still be **defined** because the
# loader resolves every ${...} placeholder and fails when the variable is entirely unset.
# APP_AUTH_TOKEN charset gate [A-Za-z0-9._-]: **kept strict** (docs/server_auth.md §5 decision).
# The old "also doubles as the client query token (URL-safe)" rationale is obsolete—the client only
# sends a sha1 digest, the plaintext secret never enters the URL; the gate is now just a loose
# constraint beyond non-emptiness, with no protocol dependency.
check_required_env() {
  local name missing="" absent="" v
  for name in APP_AUTH_TOKEN DASHSCOPE_API_KEY DASHSCOPE_API_HOST; do
    v="$(env_effective "$name")"
    [ -n "$v" ] || missing="${missing}${name} "
  done
  if [ -n "$missing" ]; then
    die "required environment variables missing or empty: ${missing} (cp .env.example .env and fill in)"
  fi
  for name in DASHSCOPE_WORKSPACE_ID HERMES_AUTH_TOKEN OPENCLAW_AUTH_TOKEN; do
    env_defined "$name" || absent="${absent}${name} "
  done
  if [ -n "$absent" ]; then
    die "required environment variables not defined: ${absent}(empty values are allowed, but the lines must exist in the shell or .env; see .env.example)"
  fi
  case "$(env_effective APP_AUTH_TOKEN)" in
    *[!A-Za-z0-9._-]*)
      die "APP_AUTH_TOKEN contains invalid characters—only letters, digits, -, _, . are allowed"
      ;;
  esac
  if [ -z "$(env_effective DASHSCOPE_WORKSPACE_ID)" ]; then
    warn "DASHSCOPE_WORKSPACE_ID is empty—X-DashScope-WorkSpace header will not be sent (TTS returns 401/411 without it)"
  fi
}

# System dependency checks: libopus (required by chat voice codec—tested with the service's own
# loader as the authoritative check) and ffmpeg (lvdata audio-analysis transcoding; missing only
# warns). Both are runtime deps where the service "starts but misbehaves", so surface them
# explicitly in the start preflight.
check_system_deps() {
  if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg not found—lvdata-mcp audio analysis (ogg→wav transcoding) will fail (query plane unaffected); install: brew install ffmpeg / apt install ffmpeg"
  fi
  if ! "$VENV/bin/python" -c 'from media.opus import _load_opus; _load_opus()' >/dev/null 2>&1; then
    die "libopus failed to load (required by chat server voice codec)—install: brew install opus (or apt install libopus0); loader candidate paths in services/media/opus.py:16-36"
  fi
}

# test environment config (configs/test, local, gitignored): fill in missing files one by one
# (generated by copying configs/dev)—rewrite rules: /opt/lvdata → <repo>/data; configs/dev/ → configs/test/.
# Ports are picked only when a file is generated: bind test, +100 steps to the next free port
# (bff syncs the lvdata port). Existing files are never touched (local changes preserved);
# a complete directory is a silent no-op.
ensure_test_config() {
  local f srv_port mcp_port
  [ -f "$DEV_CFG/server.yaml" ] || die "configs/dev/server.yaml not found, cannot generate configs/test"
  mkdir -p "$TEST_CFG"
  if [ ! -f "$TEST_CFG/server.yaml" ]; then
    srv_port="$(pick_free_port 8080)"
    mcp_port=""
    if [ -f "$TEST_CFG/lvdata_mcp.yaml" ]; then
      mcp_port="$(server_port_of "$TEST_CFG/lvdata_mcp.yaml")"
    fi
    [ -n "$mcp_port" ] || mcp_port="$(pick_free_port 8090)"
    info "Generating configs/test/server.yaml (ports server=${srv_port} / lvdata=${mcp_port})"
    sed -e "s|/opt/lvdata|$ROOT/data|g" -e "s|configs/dev/|configs/test/|g" \
      -e "s|^  port: 8080|  port: $srv_port|" \
      -e "s|127.0.0.1:8090|127.0.0.1:$mcp_port|g" \
      -e 's|^    dir: "".*$|    dir: "logs/test"   # test-instance log directory|' \
      "$DEV_CFG/server.yaml" >"$TEST_CFG/server.yaml"
  fi
  if [ ! -f "$TEST_CFG/lvdata_mcp.yaml" ]; then
    mcp_port="$(pick_free_port 8090)"
    info "Generating configs/test/lvdata_mcp.yaml (port lvdata=${mcp_port})"
    sed -e "s|/opt/lvdata|$ROOT/data|g" -e "s|configs/dev/|configs/test/|g" \
      -e "s|^  port: 8090|  port: $mcp_port|" \
      -e 's|^    dir: "".*$|    dir: "logs/test"   # test-instance log directory|' \
      "$DEV_CFG/lvdata_mcp.yaml" >"$TEST_CFG/lvdata_mcp.yaml"
  fi
  mkdir -p "$TEST_CFG/prompts"
  for f in "$DEV_CFG"/prompts/*.txt; do
    local base
    base="$(basename "$f")"
    [ -f "$TEST_CFG/prompts/$base" ] || cp "$f" "$TEST_CFG/prompts/$base"
  done
  for f in cert.pem key.pem; do
    [ -f "$TEST_CFG/$f" ] || cp "$DEV_CFG/$f" "$TEST_CFG/$f"
  done
}

# -k is harmless for http and still allows the dev self-signed cert for https
probe() { curl -ksf --max-time 3 -o /dev/null "$1" >/dev/null 2>&1; }
srv_health_url() { printf '%s://127.0.0.1:%s/healthz' "$SRV_SCHEME" "$SRV_PORT"; }
mcp_health_url() { printf 'http://127.0.0.1:%s/healthz' "$MCP_PORT"; }

# Non-loopback local IPv4: UDP connect to 192.0.2.1 (TEST-NET-1, sends no packets) to resolve the
# default route and take the source address. Offline / no default route / no venv python → empty
# output (caller falls back to 127.0.0.1)—docs/livachat_start.md §3.5
local_ip() {
  [ -x "$VENV/bin/python" ] || return 0
  "$VENV/bin/python" -c '
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.0.2.1", 80))
    print(s.getsockname()[0])
except OSError:
    pass
finally:
    s.close()
' 2>/dev/null || true
}

# Host shown in the status health line / start ready line. Follow server.host: wildcard binds
# (0.0.0.0/::) show the LAN IP so phones can connect; loopback binds show 127.0.0.1; an explicit
# host is shown as configured. Display layer only—health probes/conflict prechecks still use
# srv_health_url (127.0.0.1)—docs/livachat_start.md §3.5
srv_display_host() {
  local h="${SRV_HOST:-0.0.0.0}" l ip
  l="$(printf '%s' "$h" | tr '[:upper:]' '[:lower:]')"
  case "$l" in
    ""|0.0.0.0|::|"[::]")
      ip="$(local_ip)"
      printf '%s' "${ip:-127.0.0.1}"
      ;;
    localhost|127.0.0.1|::1|"[::1]")
      printf '127.0.0.1'
      ;;
    \[*\])
      printf '%s' "$h"
      ;;
    *:*)
      printf '[%s]' "$h"   # bare IPv6 literal needs brackets in a URL
      ;;
    *)
      printf '%s' "$h"
      ;;
  esac
}

srv_display_url() {
  printf '%s://%s:%s/healthz' "$SRV_SCHEME" "$(srv_display_host)" "$SRV_PORT"
}

# Print the pid of a live process; pid file missing / process gone → exit 1
pid_of() {
  local f="$1" pid
  [ -f "$f" ] || return 1
  pid="$(cat "$f" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    printf '%s' "$pid"
    return 0
  fi
  return 1
}

# Whether the reload parent still has a "service child process" (alive, non-zombie, and not the
# persistent multiprocessing resource_tracker—measured: under the uvicorn reloader resource_tracker
# stays resident, so judging liveness by "has any child process" would be wrong)
has_server_child() {
  ps -ax -o pid=,ppid=,stat=,command= 2>/dev/null | awk -v p="$1" '
    $2 == p && $3 !~ /^Z/ && $0 !~ /resource_tracker/ { found = 1 }
    END { exit(found ? 0 : 1) }'
}

# List of direct child pids (zombies excluded; the SIGKILL fallback path in stop uses it to kill
# orphaned children)
child_pids() {
  ps -ax -o pid=,ppid=,stat= 2>/dev/null | awk -v p="$1" '$2 == p && $3 !~ /^Z/ { print $1 }'
}

# <health-url> <pid> <seconds> [child-watch]: 0 ready / 1 timeout / 2 process or reload child exited
# child-watch non-empty (reload mode): 5s after start (wall clock), if the parent has no "service
# child process" (has_server_child; child crashed while the reload parent stays resident), fail
# early instead of being masked by the 30s timeout
wait_ready() {
  local url="$1" pid="$2" secs="$3" watch="${4:-}" i=0 t0=$SECONDS
  while [ "$i" -lt "$secs" ]; do
    if probe "$url"; then return 0; fi
    if ! kill -0 "$pid" 2>/dev/null; then return 2; fi
    if [ -n "$watch" ] && [ "$((SECONDS - t0))" -ge 5 ] && ! has_server_child "$pid"; then
      return 2
    fi
    sleep 1
    i=$((i + 1))
  done
  if probe "$url"; then return 0; fi
  if ! kill -0 "$pid" 2>/dev/null; then return 2; fi
  if [ -n "$watch" ] && ! has_server_child "$pid"; then return 2; fi
  return 1
}

ensure_uv() {
  if ! command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"   # official uv install location (when not on shell PATH)
  fi
  if command -v uv >/dev/null 2>&1; then
    info "uv already installed: $(uv --version)"
    return 0
  fi
  info "Installing uv (official astral.sh installer)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv still unavailable after install; install manually: https://docs.astral.sh/uv/getting-started/installation/"
}

# ---------------------------------------------------------------- commands

install_base() {
  ensure_uv

  info ""
  info "== Python 3.12 (uv-managed) =="
  uv python install 3.12

  info ""
  info "== Virtual environment .venv =="
  if [ -x "$VENV/bin/python" ]; then
    info ".venv already exists, reusing (to rebuild: rm -rf .venv, then rerun install)"
    if ! "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
      warn ".venv is not Python 3.12, rebuild recommended"
    fi
  else
    # --seed includes pip/setuptools/wheel, compatible with the Makefile server-install .venv/bin/pip usage
    uv venv --python 3.12 --seed "$VENV"
  fi

  info ""
  info "== test environment config (configs/test, copied from configs/dev) =="
  ensure_test_config
  info "configs/test ready: $TEST_CFG"

  info ""
  info "== Installing server and dependencies (editable; runtime deps only, review 2026-10-12) =="
  # Do not install [test,dev] development extras (pytest/ruff/mypy); add when needed:
  #   uv pip install --python "$VENV/bin/python" -e "$ROOT/services[test,dev]"
  # Keep lvdata-mcp [vec] (required) / [image] (dev yaml's 1280 max edge takes the scaling path)
  uv pip install --python "$VENV/bin/python" \
    -e "$ROOT/packages/sdk-python" \
    -e "$ROOT/services" \
    -e "$ROOT/tools/lvdata-mcp[image,vec]"
}

cmd_install() {
  local livekit=0 skip_base=0 dry_run=0 version_given=0
  local livekit_version="$LIVEKIT_DEFAULT_VERSION"

  while [ $# -gt 0 ]; do
    case "$1" in
      --livekit) livekit=1; shift ;;
      --skip-base) skip_base=1; shift ;;
      --livekit-version)
        [ $# -ge 2 ] || die "--livekit-version requires a value"
        case "$2" in -*) die "--livekit-version requires a value" ;; esac
        livekit_version="$2"; version_given=1; shift 2 ;;
      --livekit-version=*)
        livekit_version="${1#*=}"; version_given=1; shift ;;
      --dry-run) dry_run=1; shift ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown install option: $1" ;;
    esac
  done

  livekit_version="${livekit_version#v}"
  [ "$skip_base" -eq 1 ] && [ "$livekit" -eq 0 ] && die "--skip-base requires --livekit"
  [ "$version_given" -eq 1 ] && [ "$livekit" -eq 0 ] && die "--livekit-version requires --livekit"
  if [ "$livekit" -eq 1 ]; then
    livekit_validate_version "$livekit_version"
  fi

  cd "$ROOT"
  local env_existed=0
  [ -f "$ROOT/.env" ] && env_existed=1 || true

  if [ "$dry_run" -eq 1 ]; then
    info "Dry run: no changes will be made."
    if [ "$skip_base" -eq 0 ]; then
      info "Would install Python base (uv/Python 3.12/.venv/editable packages)."
    fi
    if [ "$livekit" -eq 1 ]; then
      livekit_dry_run "$livekit_version"
    fi
    return 0
  fi

  if [ "$skip_base" -eq 0 ]; then
    install_base
  fi

  if [ "$livekit" -eq 1 ]; then
    info ""
    info "== LiveKit Server =="
    livekit_install "$livekit_version"
  fi

  info ""
  if [ "$skip_base" -eq 0 ]; then
    info "Install complete."
    if [ "$env_existed" -eq 1 ] && [ -f "$ROOT/.env" ]; then
      info "Next: ./livachat.sh start (.env already exists)"
    else
      info "Next steps:"
      info "  1) cp .env.example .env (if missing) and fill in DASHSCOPE_API_KEY / DASHSCOPE_API_HOST /"
      info "     DASHSCOPE_WORKSPACE_ID / APP_AUTH_TOKEN"
      info "  2) ./livachat.sh start"
    fi
  else
    info "LiveKit install complete."
    info "Next: edit .env and set LIVACHAT__MEETING__LIVEKIT_SERVER=wss://..."
  fi
}

# ---------------------------------------------------------------- LiveKit

livekit_cleanup() {
  [ -n "$LIVEKIT_TMP_DIR" ] && rm -rf "$LIVEKIT_TMP_DIR"
  [ -n "$LIVEKIT_ENV_TMP" ] && rm -f "$LIVEKIT_ENV_TMP"
  [ -n "$LIVEKIT_CONFIG_TMP" ] && rm -f "$LIVEKIT_CONFIG_TMP"
  return 0
}

livekit_validate_version() {
  local v="$1"
  if [[ ! "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]]; then
    die "invalid LiveKit version: $v"
  fi
}

livekit_platform() {
  case "$(uname -s)" in
    Linux) LIVEKIT_OS=linux ;;
    Darwin) LIVEKIT_OS=darwin ;;
    *)
      die "unsupported platform: $(uname -s); use Linux/macOS (WSL is Linux) or set LIVEKIT_SERVER_BIN"
      ;;
  esac
}

livekit_binary_version() {
  local bin="$1" out
  [ -x "$bin" ] || return 1
  out="$("$bin" --version 2>/dev/null)" || return 1
  printf '%s\n' "$out" | sed -n 's/^livekit-server version //p' | head -n1
}

livekit_version_ok() {
  local actual="$1" target="$2"
  case "$actual" in
    "$target"|"$target"-*|"$target"+*) return 0 ;;
    *) return 1 ;;
  esac
}

livekit_curl() { curl -fsSL --retry 3 --connect-timeout 10 "$@"; }

livekit_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

livekit_shell_var_set() {
  local name="$1" seen
  eval "seen=\${$name+set}"
  [ -n "$seen" ]
}

livekit_env_file_value() {
  local name="$1" line
  [ -f "$ROOT/.env" ] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$name=" "$ROOT/.env" 2>/dev/null | tail -n 1 || true)"
  [ -n "$line" ] || return 0
  line="${line#*=}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  line="${line%$'\r'}"
  case "$line" in
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
  esac
  printf '%s' "$line"
}

livekit_install_linux() {
  local ver="$1" arch asset base url archive checksums expected actual
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    armv7l|armv7) arch=armv7 ;;
    *) die "unsupported Linux architecture: $(uname -m)" ;;
  esac

  if [ -x "$LIVEKIT_BIN" ] && [ -f "$LIVEKIT_VERSION_FILE" ] && [ "$(cat "$LIVEKIT_VERSION_FILE")" = "$ver" ]; then
    if actual="$(livekit_binary_version "$LIVEKIT_BIN")" && livekit_version_ok "$actual" "$ver"; then
      info "LiveKit $ver already installed: $LIVEKIT_BIN"
      return 0
    fi
    warn "installed LiveKit binary failed version check; reinstalling"
  fi

  command -v curl >/dev/null 2>&1 || die "curl not found (required to download LiveKit)"
  base="${LIVEKIT_RELEASE_BASE:-https://github.com/livekit/livekit/releases/download}"
  base="${base%/}"
  asset="livekit_${ver}_linux_${arch}.tar.gz"
  url="$base/v${ver}/$asset"
  archive="$LIVEKIT_TMP_DIR/$asset"
  checksums="$LIVEKIT_TMP_DIR/checksums.txt"
  info "Downloading $url"
  livekit_curl -o "$archive" "$url" || die "failed to download $url"
  livekit_curl -o "$checksums" "$base/v${ver}/checksums.txt" || die "failed to download checksums.txt"
  expected="$(awk -v a="$asset" '$2 == a { print $1; exit }' "$checksums")"
  [ -n "$expected" ] || die "checksum missing for $asset"
  [ "$(livekit_sha256 "$archive")" = "$expected" ] || die "checksum mismatch for $asset"

  mkdir -p "$LIVEKIT_TMP_DIR/extract"
  tar -xzf "$archive" -C "$LIVEKIT_TMP_DIR/extract" livekit-server LICENSE \
    || die "failed to extract $asset"
  actual="$(livekit_binary_version "$LIVEKIT_TMP_DIR/extract/livekit-server")" \
    || die "downloaded livekit-server is not executable"
  livekit_version_ok "$actual" "$ver" || die "downloaded LiveKit version '$actual' does not match $ver"

  mkdir -p "$BIN_DIR"
  install -m 755 "$LIVEKIT_TMP_DIR/extract/livekit-server" "$LIVEKIT_BIN.new"
  mv "$LIVEKIT_BIN.new" "$LIVEKIT_BIN"
  install -m 644 "$LIVEKIT_TMP_DIR/extract/LICENSE" "$BIN_DIR/LICENSE.new"
  mv "$BIN_DIR/LICENSE.new" "$BIN_DIR/LICENSE"
  install -m 644 "$checksums" "$BIN_DIR/checksums.txt.new"
  mv "$BIN_DIR/checksums.txt.new" "$BIN_DIR/checksums.txt"
  printf '%s\n' "$ver" > "$LIVEKIT_VERSION_FILE.new"
  chmod 644 "$LIVEKIT_VERSION_FILE.new"
  mv "$LIVEKIT_VERSION_FILE.new" "$LIVEKIT_VERSION_FILE"
  info "Installed LiveKit $ver: $LIVEKIT_BIN"
}

livekit_install_macos() {
  local ver="$1" src actual brew_prefix
  if [ -x "$LIVEKIT_BIN" ]; then
    if actual="$(livekit_binary_version "$LIVEKIT_BIN")"; then
      if ! livekit_version_ok "$actual" "$ver"; then
        warn "local LiveKit version $actual differs from requested $ver; keeping existing binary"
      fi
      mkdir -p "$BIN_DIR"
      printf '%s\n' "$actual" > "$LIVEKIT_VERSION_FILE.new"
      chmod 644 "$LIVEKIT_VERSION_FILE.new"
      mv "$LIVEKIT_VERSION_FILE.new" "$LIVEKIT_VERSION_FILE"
      info "LiveKit $actual already installed: $LIVEKIT_BIN"
      return 0
    fi
    warn "local LiveKit binary is not executable; reinstalling"
  fi

  if ! command -v brew >/dev/null 2>&1; then
    for src in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$src" ]; then
        PATH="$(dirname "$src"):$PATH"
        export PATH
        break
      fi
    done
  fi
  command -v brew >/dev/null 2>&1 || die "Homebrew not found; install Homebrew or set LIVEKIT_SERVER_BIN"

  if ! brew list --versions livekit >/dev/null 2>&1; then
    info "Installing Homebrew livekit..."
    HOMEBREW_NO_AUTO_UPDATE=1 brew install livekit || die "brew install livekit failed"
  fi
  brew_prefix="$(brew --prefix livekit 2>/dev/null || true)"
  [ -n "$brew_prefix" ] || die "cannot locate Homebrew livekit prefix"
  src="$brew_prefix/bin/livekit-server"
  [ -x "$src" ] || die "Homebrew livekit-server not found: $src"
  actual="$(livekit_binary_version "$src")" || die "cannot read Homebrew LiveKit version"

  mkdir -p "$BIN_DIR"
  install -m 755 "$src" "$LIVEKIT_BIN.new"
  mv "$LIVEKIT_BIN.new" "$LIVEKIT_BIN"
  printf '%s\n' "$actual" > "$LIVEKIT_VERSION_FILE.new"
  chmod 644 "$LIVEKIT_VERSION_FILE.new"
  mv "$LIVEKIT_VERSION_FILE.new" "$LIVEKIT_VERSION_FILE"
  if ! livekit_version_ok "$actual" "$ver"; then
    warn "Homebrew LiveKit version $actual differs from requested $ver; recorded actual version"
  fi
  info "Installed LiveKit $actual: $LIVEKIT_BIN"
}

livekit_install_server_bin() {
  local ver="$1" src="${LIVEKIT_SERVER_BIN:-}" actual
  [ -n "$src" ] && [ -x "$src" ] || die "LIVEKIT_SERVER_BIN is not executable: $src"
  actual="$(livekit_binary_version "$src")" || die "cannot read LIVEKIT_SERVER_BIN version"
  mkdir -p "$BIN_DIR"
  install -m 755 "$src" "$LIVEKIT_BIN.new"
  mv "$LIVEKIT_BIN.new" "$LIVEKIT_BIN"
  printf '%s\n' "$actual" > "$LIVEKIT_VERSION_FILE.new"
  chmod 644 "$LIVEKIT_VERSION_FILE.new"
  mv "$LIVEKIT_VERSION_FILE.new" "$LIVEKIT_VERSION_FILE"
  if ! livekit_version_ok "$actual" "$ver"; then
    warn "LIVEKIT_SERVER_BIN version $actual differs from requested $ver; recorded actual version"
  fi
  info "Installed LiveKit $actual from LIVEKIT_SERVER_BIN: $LIVEKIT_BIN"
}

livekit_ensure_sample() {
  local ver="$1" base url tmp
  if [ -f "$LIVEKIT_SAMPLE" ]; then
    info "config sample exists: $LIVEKIT_SAMPLE"
    return 0
  fi
  command -v curl >/dev/null 2>&1 || die "curl not found (required to download config-sample.yaml)"
  base="${LIVEKIT_RAW_BASE:-https://raw.githubusercontent.com/livekit/livekit}"
  base="${base%/}"
  url="$base/v${ver}/config-sample.yaml"
  tmp="$LIVEKIT_TMP_DIR/config-sample.yaml"
  if ! livekit_curl -o "$tmp" "$url"; then
    if [ "$ver" != "$LIVEKIT_DEFAULT_VERSION" ]; then
      warn "config sample for $ver unavailable; trying $LIVEKIT_DEFAULT_VERSION"
      url="$base/v${LIVEKIT_DEFAULT_VERSION}/config-sample.yaml"
      livekit_curl -o "$tmp" "$url" || die "failed to download config-sample.yaml"
    else
      die "failed to download config-sample.yaml"
    fi
  fi
  [ -s "$tmp" ] && grep -q '^port: 7880' "$tmp" || die "downloaded config-sample.yaml is invalid"
  mkdir -p "$BIN_DIR"
  install -m 644 "$tmp" "$LIVEKIT_SAMPLE.new"
  mv "$LIVEKIT_SAMPLE.new" "$LIVEKIT_SAMPLE"
  info "Installed config sample: $LIVEKIT_SAMPLE"
}

livekit_ensure_license() {
  local ver="$1" base url tmp brew_prefix
  [ -f "$BIN_DIR/LICENSE" ] && return 0
  if command -v brew >/dev/null 2>&1; then
    brew_prefix="$(brew --prefix livekit 2>/dev/null || true)"
    if [ -n "$brew_prefix" ] && [ -f "$brew_prefix/LICENSE" ]; then
      install -m 644 "$brew_prefix/LICENSE" "$BIN_DIR/LICENSE.new"
      mv "$BIN_DIR/LICENSE.new" "$BIN_DIR/LICENSE"
      return 0
    fi
  fi
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found; skipping LiveKit LICENSE download"
    return 0
  fi
  base="${LIVEKIT_RAW_BASE:-https://raw.githubusercontent.com/livekit/livekit}"
  base="${base%/}"
  url="$base/v${ver}/LICENSE"
  tmp="$LIVEKIT_TMP_DIR/LICENSE"
  if livekit_curl -o "$tmp" "$url"; then
    install -m 644 "$tmp" "$BIN_DIR/LICENSE.new"
    mv "$BIN_DIR/LICENSE.new" "$BIN_DIR/LICENSE"
  else
    warn "could not install LiveKit LICENSE; see $url"
  fi
}

livekit_generate_keypair() {
  local out key secret
  out="$("$LIVEKIT_BIN" generate-keys 2>/dev/null)" || die "livekit-server generate-keys failed"
  key="$(printf '%s\n' "$out" | sed -n 's/^API Key:[[:space:]]*//p' | head -n1)"
  secret="$(printf '%s\n' "$out" | sed -n 's/^API Secret:[[:space:]]*//p' | head -n1)"
  [ -n "$key" ] && [ -n "$secret" ] || die "could not parse key/secret from generate-keys"
  case "$key" in *[!A-Za-z0-9._-]*) die "generated LiveKit key contains unsupported characters" ;; esac
  case "$secret" in *[!A-Za-z0-9._-]*) die "generated LiveKit secret contains unsupported characters" ;; esac
  [ "$key" != "$secret" ] || die "generated LiveKit key and secret are identical"
  LIVEKIT_KEY="$key"
  LIVEKIT_SECRET="$secret"
}

livekit_read_yaml_pair() {
  local line key secret
  [ -f "$LIVEKIT_CONFIG" ] || return 1
  line="$(awk '
    /^keys:[[:space:]]*$/ { f=1; next }
    f && /^[[:space:]]*$/ { next }
    f && /^[[:space:]]*#/ { next }
    f { print; exit }
  ' "$LIVEKIT_CONFIG")"
  [ -n "$line" ] || return 1
  key="$(printf '%s\n' "$line" | sed -n 's/^[[:space:]]*"\([^"]*\)"[[:space:]]*:[[:space:]]*"\([^"]*\)"[[:space:]]*$/\1/p')"
  secret="$(printf '%s\n' "$line" | sed -n 's/^[[:space:]]*"\([^"]*\)"[[:space:]]*:[[:space:]]*"\([^"]*\)"[[:space:]]*$/\2/p')"
  [ -n "$key" ] && [ -n "$secret" ] || return 1
  LIVEKIT_YAML_KEY="$key"
  LIVEKIT_YAML_SECRET="$secret"
}

livekit_check_existing() {
  local fkey fsec skey ssec
  info "Existing LiveKit config preserved: $LIVEKIT_CONFIG"
  if livekit_read_yaml_pair; then
    fkey="$(livekit_env_file_value LIVACHAT__MEETING__LIVEKIT_KEY)"
    fsec="$(livekit_env_file_value LIVACHAT__MEETING__LIVEKIT_SECRET)"
    if [ -z "$fkey" ] || [ -z "$fsec" ]; then
      warn ".env key/secret is missing; livekit.yaml was not modified"
    elif [ "$fkey" != "$LIVEKIT_YAML_KEY" ] || [ "$fsec" != "$LIVEKIT_YAML_SECRET" ]; then
      warn ".env key/secret does not match livekit.yaml; not modifying .env/livekit.yaml"
    fi
    if livekit_shell_var_set LIVACHAT__MEETING__LIVEKIT_KEY; then
      skey="$(env_effective LIVACHAT__MEETING__LIVEKIT_KEY)"
      [ "$skey" = "$LIVEKIT_YAML_KEY" ] || warn "shell LIVEKIT_KEY differs from livekit.yaml; not modifying .env/livekit.yaml"
    fi
    if livekit_shell_var_set LIVACHAT__MEETING__LIVEKIT_SECRET; then
      ssec="$(env_effective LIVACHAT__MEETING__LIVEKIT_SECRET)"
      [ "$ssec" = "$LIVEKIT_YAML_SECRET" ] || warn "shell LIVEKIT_SECRET differs from livekit.yaml; not modifying .env/livekit.yaml"
    fi
  else
    warn "could not parse keys from livekit.yaml; not modifying .env/livekit.yaml"
  fi
  [ -f "$LIVEKIT_SAMPLE" ] || warn "$LIVEKIT_SAMPLE is missing; fetch it manually or delete livekit.yaml and rerun (regenerates keys)"
  [ -f "$BIN_DIR/LICENSE" ] || warn "$BIN_DIR/LICENSE is missing; fetch it manually or delete livekit.yaml and rerun (regenerates keys)"
}

livekit_first_install() {
  local had_pair=0 env_created=0 backup ts old_umask
  old_umask="$(umask)"
  umask 077
  if [ -L "$ROOT/.env" ]; then
    die "$ROOT/.env is a symlink; resolve it manually before running install --livekit"
  fi
  if [ -f "$ROOT/.env" ]; then
    grep -Eq '^[[:space:]]*(export[[:space:]]+)?LIVACHAT__MEETING__LIVEKIT_(KEY|SECRET)[[:space:]]*=' "$ROOT/.env" \
      && had_pair=1 || true
  else
    if [ -f "$ROOT/.env.example" ]; then
      cp "$ROOT/.env.example" "$ROOT/.env"
    else
      : > "$ROOT/.env"
    fi
    chmod 600 "$ROOT/.env"
    env_created=1
  fi

  mkdir -p "$BIN_DIR"
  LIVEKIT_CONFIG_TMP="$(mktemp "$BIN_DIR/.livekit.yaml.XXXXXX")"
  chmod 600 "$LIVEKIT_CONFIG_TMP"
  cat > "$LIVEKIT_CONFIG_TMP" <<EOF
# Generated by ./livachat.sh install --livekit.
# Single-node only: Redis / cluster mode is intentionally not supported.
# Keep the key pair in sync with repo-root .env:
#   LIVACHAT__MEETING__LIVEKIT_KEY / LIVACHAT__MEETING__LIVEKIT_SECRET
port: 7880

rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true

room:
  empty_timeout: 300
  departure_timeout: 300
  max_participants: 100

keys:
  "$LIVEKIT_KEY": "$LIVEKIT_SECRET"

logging:
  level: info
EOF

  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="$ROOT/.env.bak.$ts"
  cp "$ROOT/.env" "$backup"
  chmod 600 "$backup"

  LIVEKIT_ENV_TMP="$(mktemp "$ROOT/.env.tmp.XXXXXX")"
  chmod 600 "$LIVEKIT_ENV_TMP"
  awk '
    /^[[:space:]]*(export[[:space:]]+)?LIVACHAT__MEETING__LIVEKIT_(KEY|SECRET)[[:space:]]*=/ { next }
    { sub(/\r$/, ""); print }
  ' "$ROOT/.env" > "$LIVEKIT_ENV_TMP"
  {
    printf '# Meeting / LiveKit key pair (generated by livachat.sh install --livekit)\n'
    printf 'LIVACHAT__MEETING__LIVEKIT_KEY=%s\n' "$LIVEKIT_KEY"
    printf 'LIVACHAT__MEETING__LIVEKIT_SECRET=%s\n' "$LIVEKIT_SECRET"
  } >> "$LIVEKIT_ENV_TMP"
  mv "$LIVEKIT_ENV_TMP" "$ROOT/.env"
  LIVEKIT_ENV_TMP=""

  if [ -e "$LIVEKIT_CONFIG" ]; then
    if [ "$env_created" -eq 1 ]; then
      rm -f "$ROOT/.env"
    else
      cp "$backup" "$ROOT/.env"
      chmod 600 "$ROOT/.env"
    fi
    die "$LIVEKIT_CONFIG appeared concurrently; aborting without overwriting it"
  fi
  if ! mv "$LIVEKIT_CONFIG_TMP" "$LIVEKIT_CONFIG"; then
    if [ "$env_created" -eq 1 ]; then
      rm -f "$ROOT/.env"
    else
      cp "$backup" "$ROOT/.env"
      chmod 600 "$ROOT/.env"
    fi
    die "failed to install $LIVEKIT_CONFIG"
  fi
  LIVEKIT_CONFIG_TMP=""

  info "LiveKit key pair generated: $LIVEKIT_CONFIG and $ROOT/.env"
  if [ "$had_pair" -eq 1 ]; then
    info "Previous .env key/secret replaced (backup: $backup)"
  fi
  info "LIVACHAT__MEETING__LIVEKIT_SERVER was not modified; edit .env and set it to the local wss:// URL."
  umask "$old_umask"
}

livekit_dry_run() {
  local ver="$1" first=1
  livekit_validate_version "$ver"
  if [ -n "${LIVEKIT_SERVER_BIN:-}" ]; then
    LIVEKIT_OS="server-bin"
  else
    livekit_platform
  fi
  [ -f "$LIVEKIT_CONFIG" ] && first=0
  info "Would install LiveKit $ver on $LIVEKIT_OS."
  if [ -n "${LIVEKIT_SERVER_BIN:-}" ]; then
    info "Would copy LIVEKIT_SERVER_BIN=$LIVEKIT_SERVER_BIN."
  elif [ "$LIVEKIT_OS" = "linux" ]; then
    info "Would download/verify the release tarball if needed."
  else
    info "Would use Homebrew if no local binary exists."
  fi
  if [ "$first" -eq 1 ]; then
    info "First install: would generate a key pair, update .env key/secret, and create livekit.yaml."
    if livekit_shell_var_set LIVACHAT__MEETING__LIVEKIT_KEY || livekit_shell_var_set LIVACHAT__MEETING__LIVEKIT_SECRET; then
      info "Would abort: shell LIVEKIT_KEY/SECRET is set."
    fi
  else
    info "Existing livekit.yaml: would not generate keys or modify .env/livekit.yaml."
  fi
  info "LIVACHAT__MEETING__LIVEKIT_SERVER would not be modified."
}

livekit_install() {
  local ver="$1" actual
  livekit_validate_version "$ver"
  if [ -n "${LIVEKIT_SERVER_BIN:-}" ]; then
    LIVEKIT_OS="server-bin"
  else
    livekit_platform
  fi

  if [ ! -f "$LIVEKIT_CONFIG" ]; then
    if [ -L "$ROOT/.env" ]; then
      die "$ROOT/.env is a symlink; resolve it manually before running install --livekit"
    fi
    if livekit_shell_var_set LIVACHAT__MEETING__LIVEKIT_KEY || livekit_shell_var_set LIVACHAT__MEETING__LIVEKIT_SECRET; then
      die "shell has LIVACHAT__MEETING__LIVEKIT_KEY/SECRET set; unset before first install"
    fi
  fi

  mkdir -p "$BIN_DIR"
  chmod 755 "$BIN_DIR"
  LIVEKIT_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/livachat-livekit.XXXXXX")"
  trap 'livekit_cleanup' EXIT
  trap 'livekit_cleanup; exit 130' INT
  trap 'livekit_cleanup; exit 143' TERM

  if [ -n "${LIVEKIT_SERVER_BIN:-}" ]; then
    livekit_install_server_bin "$ver"
  elif [ "$LIVEKIT_OS" = "linux" ]; then
    livekit_install_linux "$ver"
  else
    livekit_install_macos "$ver"
  fi

  actual="$(cat "$LIVEKIT_VERSION_FILE" 2>/dev/null || true)"
  [ -n "$actual" ] || actual="$ver"

  if [ -f "$LIVEKIT_CONFIG" ]; then
    livekit_check_existing
  else
    livekit_ensure_sample "$actual"
    livekit_ensure_license "$actual"
    livekit_generate_keypair
    livekit_first_install
  fi

  livekit_cleanup
  trap - EXIT INT TERM
}

cmd_start() {
  cd "$ROOT"
  command -v curl >/dev/null 2>&1 || die "curl not found"
  [ -x "$VENV/bin/livachat-server" ] || die "not installed: .venv/bin/livachat-server missing, run ./livachat.sh install first"
  [ -x "$VENV/bin/lvdata-mcp" ]      || die "not installed: .venv/bin/lvdata-mcp missing, run ./livachat.sh install first"
  { [ -f "$TEST_CFG/server.yaml" ] && [ -f "$TEST_CFG/lvdata_mcp.yaml" ]; } \
    || die "configs/test missing/incomplete (generated locally, gitignored)—run ./livachat.sh install to fill in; see docs/livachat_start.md"
  mkdir -p "$ROOT/data"   # test data root (parent dir of the chat sqlite must exist; fail fast)
  check_required_env
  check_system_deps

  mkdir -p "$RUN_DIR" "$LOG_DIR"
  resolve_ports
  local srv_url srv_disp mcp_url pid r rc=0 reload_watch=""
  srv_url="$(srv_health_url)"
  srv_disp="$(srv_display_url)"
  mcp_url="$(mcp_health_url)"
  # Reload-mode detection matches main.py (on by default, off with "0/false/no"): watch child liveness under reload
  case "$(printf '%s' "${LIVACHAT_SERVER_RELOAD:-1}" | tr '[:upper:]' '[:lower:]')" in
    0 | false | no) ;;
    *) if command -v ps >/dev/null 2>&1; then reload_watch=1; fi ;;
  esac

  # Conflict precheck: health endpoint responds but no pid record for this checkout → occupied by another instance
  if ! pid_of "$SRV_PID" >/dev/null && probe "$srv_url"; then
    die "port $SRV_PORT already responds (another worktree or manually started instance); stop it before starting"
  fi
  if ! pid_of "$MCP_PID" >/dev/null && probe "$mcp_url"; then
    die "port $MCP_PORT already responds (another worktree or manually started instance); stop it before starting"
  fi

  # livachat-server (hot reload on by default; disable with LIVACHAT_SERVER_RELOAD=0)
  if pid="$(pid_of "$SRV_PID")"; then
    info "livachat-server already running (pid $pid), skipping"
  else
    info "Starting livachat-server ..."
    # Full logs are written by the in-process RotatingFileHandler to logs/test/livachat.log (env
    # overrides pin the test subdirectory, isolating from manual dev instances; the process only
    # writes files, stdout has no log output—redirect kept only to prevent nohup.out); stderr→boot
    # file (truncated on each start: early startup, warnings, native crash output)—docs/server_logging.md §8
    LIVA_CHAT_CONFIG="$TEST_CFG/server.yaml" LIVACHAT__OBSERVABILITY__LOG_FILE__DIR="logs/test" \
      nohup "$VENV/bin/livachat-server" >/dev/null 2>"$SRV_BOOT" &
    pid=$!
    echo "$pid" >"$SRV_PID"
    r=0
    wait_ready "$srv_url" "$pid" 30 "$reload_watch" || r=$?
    if [ "$r" -eq 0 ]; then
      info "livachat-server ready: $srv_disp (pid $pid)"
    elif [ "$r" -eq 2 ]; then
      if kill -0 "$pid" 2>/dev/null; then
        warn "livachat-server child process exited (reload parent still alive); see logs: tail -n 50 $SRV_BOOT / $SRV_LOG"
      else
        warn "livachat-server exited right after start (usually early startup failure); see logs: tail -n 50 $SRV_BOOT / $SRV_LOG"
      fi
      rc=1
    else
      warn "livachat-server did not pass health check within 30s; see logs: tail -n 50 $SRV_LOG"
      rc=1
    fi
  fi

  # lvdata-mcp serve
  if pid="$(pid_of "$MCP_PID")"; then
    info "lvdata-mcp already running (pid $pid), skipping"
  else
    info "Starting lvdata-mcp ..."
    # Same as the server block: full logs by the in-process RotatingFileHandler to logs/test/lvdata_mcp.log
    # (env override pins the test subdirectory; redirect kept to prevent nohup.out), stderr→boot
    # (truncated each start)—docs/server_logging.md §8
    LVDATA_MCP_CONFIG="$TEST_CFG/lvdata_mcp.yaml" LVDATA_MCP__SERVER__LOG_FILE__DIR="logs/test" \
      nohup "$VENV/bin/lvdata-mcp" serve >/dev/null 2>"$MCP_BOOT" &
    pid=$!
    echo "$pid" >"$MCP_PID"
    r=0
    wait_ready "$mcp_url" "$pid" 30 || r=$?
    if [ "$r" -eq 0 ]; then
      info "lvdata-mcp ready: $mcp_url (pid $pid)"
    elif [ "$r" -eq 2 ]; then
      warn "lvdata-mcp exited right after start (usually early startup failure); see logs: tail -n 50 $MCP_BOOT / $MCP_LOG"
      rc=1
    else
      warn "lvdata-mcp did not pass health check within 30s; see logs: tail -n 50 $MCP_LOG"
      rc=1
    fi
  fi

  exit "$rc"
}

stop_one() {
  local name="$1" f="$2" pid i
  if ! pid="$(pid_of "$f")"; then
    if [ -f "$f" ]; then
      info "$name: process gone, removing stale pid file"
      rm -f "$f"
    else
      info "$name: not running"
    fi
    return 0
  fi
  # PID-reuse guard: only kill a process whose command line matches the service name
  if ! ps -p "$pid" -o command= 2>/dev/null | grep -qF "$name"; then
    warn "$name: pid $pid does not match expected process (PID reuse?), skipping kill and removing pid file"
    rm -f "$f"
    return 0
  fi
  info "$name: stopping (pid $pid) ..."
  # Record direct children: when the SIGKILL fallback kills the parent, children are not
  # terminated and get orphaned, still holding the port
  local children cp alive
  children="$(child_pids "$pid")"
  kill "$pid" 2>/dev/null || true
  i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "$name: still running after 10s, sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
  alive=""
  for cp in $children; do
    if kill -0 "$cp" 2>/dev/null; then alive="$alive $cp"; fi
  done
  if [ -n "$alive" ]; then
    warn "$name: cleaning up leftover child processes:${alive}"
    kill $alive 2>/dev/null || true
    sleep 1
    for cp in $alive; do
      if kill -0 "$cp" 2>/dev/null; then kill -9 "$cp" 2>/dev/null || true; fi
    done
  fi
  rm -f "$f"
  if kill -0 "$pid" 2>/dev/null; then
    warn "$name: still not exited, please check pid $pid manually"
    return 1
  fi
  info "$name: stopped"
  return 0
}

cmd_stop() {
  cd "$ROOT"
  local rc=0
  stop_one "livachat-server" "$SRV_PID" || rc=1
  stop_one "lvdata-mcp" "$MCP_PID" || rc=1
  exit "$rc"
}

# <name> <pid-file> <probe-url> <log> [display-url]: probes always use probe-url (loopback); display
# may use display-url (server passes LAN IP; defaults to probe-url)—docs/livachat_start.md §3.5
status_one() {
  local name="$1" f="$2" url="$3" log="$4" disp="${5:-$3}" pid
  if pid="$(pid_of "$f")"; then
    if probe "$url"; then
      printf '%-16s running (healthy)  pid=%s  %s\n' "$name" "$pid" "$disp"
      return 0
    fi
    printf '%-16s running (health check failed)  pid=%s  log: %s\n' "$name" "$pid" "$log"
    return 1
  fi
  if probe "$url"; then
    printf '%-16s not managed by this script, but %s responds (another worktree / manually started instance)\n' "$name" "$url"
    return 1
  fi
  printf '%-16s not running\n' "$name"
  return 1
}

cmd_status() {
  cd "$ROOT"
  command -v curl >/dev/null 2>&1 || die "curl not found"
  resolve_ports
  local rc=0
  status_one "livachat-server" "$SRV_PID" "$(srv_health_url)" "$SRV_LOG" "$(srv_display_url)" || rc=1
  status_one "lvdata-mcp" "$MCP_PID" "$(mcp_health_url)" "$MCP_LOG" || rc=1
  exit "$rc"
}

case "${1:-}" in
  install) shift; cmd_install "$@" ;;
  start)   "cmd_start" ;;
  stop)    "cmd_stop" ;;
  status)  "cmd_status" ;;
  ""|-h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
