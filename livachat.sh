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

# Ports come from configs/test (resolve_ports); fall back to the defaults recorded in REPO.md if unreadable
SRV_PORT=8080
MCP_PORT=8090

info() { printf '%s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
LivaChat local service manager (chat server + lvdata-mcp)

Usage: ./livachat.sh <command>

  install   Install uv and Python 3.12, create .venv, editable-install services and dependencies
  start     Start both services in background and wait for health checks
  stop      Gracefully stop both services (SIGTERM, SIGKILL after 10s if still running)
  status    Show process and /healthz status; exit 0 when all running and healthy, otherwise 1
EOF
}

# ---------------------------------------------------------------- utilities

# Read ports from the actual config (guarantees consistency with configs/test rather than guessing)
resolve_ports() {
  local p
  if [ -x "$VENV/bin/python" ]; then
    p="$(cd "$ROOT" && "$VENV/bin/python" -c 'import yaml;print(yaml.safe_load(open("configs/test/server.yaml"))["server"]["port"])' 2>/dev/null || true)"
    if [ -n "$p" ]; then SRV_PORT="$p"; fi
    p="$(cd "$ROOT" && "$VENV/bin/python" -c 'import yaml;print(yaml.safe_load(open("configs/test/lvdata_mcp.yaml"))["server"]["port"])' 2>/dev/null || true)"
    if [ -n "$p" ]; then MCP_PORT="$p"; fi
  fi
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
  case "$line" in
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
  esac
  printf '%s' "$line"
}

# start preflight: check required items one by one (.env absent or required item empty → list and die)
# Basis: install_design.md §1.4; chat validation.py:30-33 (dev_token non-empty),
# lvdata validate_required (api_key non-empty). WORKSPACE_ID empty string allowed per §1.4 → warning only.
# APP_AUTH_TOKEN charset gate [A-Za-z0-9._-]: **kept strict** (docs/server_auth.md §5 decision).
# The old "also doubles as the client query token (URL-safe)" rationale is obsolete—the client only
# sends a sha1 digest, the plaintext secret never enters the URL; the gate is now just a loose
# constraint beyond non-emptiness, with no protocol dependency.
check_required_env() {
  local name missing="" v
  for name in APP_AUTH_TOKEN DASHSCOPE_API_KEY DASHSCOPE_API_HOST; do
    v="$(env_effective "$name")"
    [ -n "$v" ] || missing="${missing}${name} "
  done
  if [ -n "$missing" ]; then
    die "required environment variables missing or empty: ${missing} (cp .env.example .env and fill in)"
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

probe() { curl -ksf --max-time 3 -o /dev/null "$1" >/dev/null 2>&1; }
srv_health_url() { printf 'https://127.0.0.1:%s/healthz' "$SRV_PORT"; }
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

# server display address (status health line / start ready line; 127.0.0.1 if no LAN IP, so phones
# can use host:port). Display layer only—health probes/conflict prechecks still use srv_health_url
# (127.0.0.1)—docs/livachat_start.md §3.5
srv_display_url() {
  local ip
  ip="$(local_ip)"
  printf 'https://%s:%s/healthz' "${ip:-127.0.0.1}" "$SRV_PORT"
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

cmd_install() {
  cd "$ROOT"
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

  info ""
  info "Install complete."
  if [ -f "$ROOT/.env" ]; then
    info "Next: ./livachat.sh start (.env already exists)"
  else
    info "Next steps:"
    info "  1) cp .env.example .env and fill in DASHSCOPE_API_KEY / DASHSCOPE_API_HOST /"
    info "     DASHSCOPE_WORKSPACE_ID / APP_AUTH_TOKEN"
    info "  2) ./livachat.sh start"
  fi
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
  install) "cmd_install" ;;
  start)   "cmd_start" ;;
  stop)    "cmd_stop" ;;
  status)  "cmd_status" ;;
  ""|-h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
