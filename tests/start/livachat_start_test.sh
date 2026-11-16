#!/usr/bin/env bash
# CLI-level tests for `livachat.sh start/status` health-scheme/display-host resolution and start
# env preflight.
#
# Each case runs a disposable copy of the script with fake console scripts and a fake curl.
# The sandbox carries a copy of services/shared so the real load_config() reads that sandbox's
# configs/test and .env (PYTHONPATH points at the copy); no real service is started and no
# network request is made. docs/livachat_start.md §3.8 / §3.3.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIVACHAT="$REPO_ROOT/livachat.sh"
REAL_PY="$REPO_ROOT/.venv/bin/python"
SRV_PORT=18180
MCP_PORT=18190
TMP_ROOT="${TMPDIR:-/tmp}"
TMP_ROOT="${TMP_ROOT%/}"
T="$(mktemp -d "$TMP_ROOT/livachat-start-test.XXXXXX")"
T="$(cd "$T" && pwd -P)"

cleanup() {
  local f pid
  for f in "$T"/*/.run/*.pid; do
    [ -f "$f" ] || continue
    pid="$(cat "$f" 2>/dev/null || true)"
    [ -n "$pid" ] || continue
    if ps -p "$pid" -o command= 2>/dev/null | grep -qF "$T/"; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  rm -rf "$T"
}
trap cleanup EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf 'ok: %s\n' "$*"; }

[ -x "$REAL_PY" ] || { printf 'SKIP: %s not found (run ./livachat.sh install)\n' "$REAL_PY"; exit 0; }
[ -f "$REPO_ROOT/services/shared/config/loader.py" ] || fail "services/shared not found"
command -v awk >/dev/null 2>&1 || fail "awk not found"

# new_repo <name> <yaml-ssl> [dotenv-ssl] [server-host]
new_repo() {
  local name="$1" yaml_ssl="$2" dotenv_ssl="${3:-}" server_host="${4:-0.0.0.0}" d
  d="$T/$name"
  mkdir -p "$d/configs/test" "$d/.venv/bin" "$d/fakebin" "$d/services" "$d/data"
  cp "$LIVACHAT" "$d/livachat.sh"
  chmod +x "$d/livachat.sh"
  ln -s "$REAL_PY" "$d/.venv/bin/python"
  # A bare symlink loses the venv prefix; pyvenv.cfg + lib symlink keep the real site-packages
  cp "$REPO_ROOT/.venv/pyvenv.cfg" "$d/.venv/pyvenv.cfg"
  ln -s "$REPO_ROOT/.venv/lib" "$d/.venv/lib"
  cp -R "$REPO_ROOT/services/shared" "$d/services/"

  awk -v port="$SRV_PORT" -v ssl="$yaml_ssl" -v host="$server_host" '
    /^server:[[:space:]]*$/ { in_server = 1; print; next }
    in_server && /^[^#[:space:]]/ { in_server = 0 }
    in_server && /^  host:/ { print "  host: \"" host "\""; next }
    in_server && /^  port:/ { print "  port: " port; next }
    in_server && /^  ssl:/ { in_ssl = 1; print; next }
    in_ssl && /^    enabled:/ { print "    enabled: " ssl; in_ssl = 0; next }
    { print }
  ' "$REPO_ROOT/configs/dev/server.yaml" |
    awk '
      /^meeting:[[:space:]]*$/ { in_meeting = 1; print; next }
      in_meeting && /^[^#[:space:]]/ { in_meeting = 0 }
      in_meeting && /^  enabled:/ { print "  enabled: false"; next }
      { print }
    ' > "$d/configs/test/server.yaml"

  awk -v port="$MCP_PORT" '
    /^server:[[:space:]]*$/ { in_server = 1; print; next }
    in_server && /^[^#[:space:]]/ { in_server = 0 }
    in_server && /^  port:/ { print "  port: " port; next }
    { print }
  ' "$REPO_ROOT/configs/dev/lvdata_mcp.yaml" > "$d/configs/test/lvdata_mcp.yaml"

  cat > "$d/.env" <<'EOF'
APP_AUTH_TOKEN=test-token
DASHSCOPE_API_KEY=test-key
DASHSCOPE_API_HOST=example.invalid
DASHSCOPE_WORKSPACE_ID=
HERMES_AUTH_TOKEN=
OPENCLAW_AUTH_TOKEN=
LIVACHAT__MEETING__ENABLED=false
EOF
  if [ -n "$dotenv_ssl" ]; then
    printf 'LIVACHAT__SERVER__SSL__ENABLED=%s\n' "$dotenv_ssl" >> "$d/.env"
  fi

  cat > "$d/.venv/bin/livachat-server" <<'EOF'
#!/usr/bin/env bash
trap 'exit 0' TERM INT
while :; do sleep 1; done
EOF
  cat > "$d/.venv/bin/lvdata-mcp" <<'EOF'
#!/usr/bin/env bash
trap 'exit 0' TERM INT
while :; do sleep 1; done
EOF
  chmod +x "$d/.venv/bin/livachat-server" "$d/.venv/bin/lvdata-mcp"

  # Probe success only after start writes the pid file; initial conflict prechecks see no pid
  # and must get a failure, otherwise start would refuse to run.
  cat > "$d/fakebin/curl" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$d/curl.log"
for a in "\$@"; do
  case "\$a" in
    "https://127.0.0.1:$SRV_PORT/healthz"|"http://127.0.0.1:$SRV_PORT/healthz")
      [ -f "$d/.run/livachat.pid" ] && exit 0 || exit 7
      ;;
    "http://127.0.0.1:$MCP_PORT/healthz")
      [ -f "$d/.run/lvdata_mcp.pid" ] && exit 0 || exit 7
      ;;
  esac
done
exit 0
EOF
  chmod +x "$d/fakebin/curl"
  printf '%s\n' "$d"
}

# Keep host env from leaking into the sandbox. PATH/PYTHONPATH point at the sandbox; the copied
# shared package makes _project_root() resolve inside the sandbox, so .env overrides are tested.
run_cli() {
  local d="$1" sub="$2"
  shift 2
  (
    cd "$d"
    env \
      -u APP_AUTH_TOKEN -u DASHSCOPE_API_KEY -u DASHSCOPE_API_HOST \
      -u DASHSCOPE_WORKSPACE_ID -u HERMES_AUTH_TOKEN -u OPENCLAW_AUTH_TOKEN \
      -u LIVACHAT__SERVER__SSL__ENABLED -u LIVACHAT__MEETING__ENABLED \
      -u LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR -u LIVACHAT__MEETING__LIVEKIT_KEY \
      -u LIVACHAT__MEETING__LIVEKIT_SECRET -u LIVACHAT__MEETING__LIVEKIT_SERVER \
      -u LIVA_CHAT_CONFIG -u LVDATA_MCP_CONFIG \
      "$@" \
      PATH="$d/fakebin:$PATH" PYTHONPATH="$d/services" LIVACHAT_SERVER_RELOAD=0 \
      ./livachat.sh "$sub"
  )
}

run_rc() { # <out-file> <d> <sub> [env assignments...]
  local out="$1" d="$2" sub="$3"
  shift 3
  if run_cli "$d" "$sub" "$@" >"$out" 2>&1; then
    printf '0'
  else
    printf '%s' "$?"
  fi
}

assert_contains() { # <file> <literal> <label>
  grep -Fq -- "$2" "$1" || {
    printf '%s\n' "--- $1 ---" >&2
    cat "$1" >&2
    fail "$3"
  }
}

assert_not_contains() {
  if grep -Fq -- "$2" "$1"; then
    printf '%s\n' "--- $1 ---" >&2
    cat "$1" >&2
    fail "$3"
  fi
}

# start/status/stop with the expected scheme, plus the lvdata endpoint staying http.
test_scheme() { # <name> <yaml-ssl> <dotenv-ssl> <shell-ssl> <expected>
  local name="$1" yaml_ssl="$2" dotenv_ssl="$3" shell_ssl="$4" expected="$5"
  local d rc start_out status_out
  d="$(new_repo "$name" "$yaml_ssl" "$dotenv_ssl")"
  start_out="$d/start.log"
  status_out="$d/status.log"

  if [ -n "$shell_ssl" ]; then
    rc="$(run_rc "$start_out" "$d" start "LIVACHAT__SERVER__SSL__ENABLED=$shell_ssl")"
  else
    rc="$(run_rc "$start_out" "$d" start)"
  fi
  [ "$rc" = 0 ] || { cat "$start_out" >&2; fail "$name: start rc=$rc"; }
  assert_contains "$start_out" "livachat-server ready: $expected://" "$name: ready line scheme"
  assert_contains "$start_out" ":$SRV_PORT/healthz" "$name: ready line port"
  assert_contains "$start_out" "lvdata-mcp ready: http://127.0.0.1:$MCP_PORT/healthz" "$name: lvdata fixed http"
  assert_contains "$d/curl.log" "$expected://127.0.0.1:$SRV_PORT/healthz" "$name: probe URL scheme"
  assert_contains "$d/curl.log" "-ksf" "$name: probe keeps -k for self-signed certs"
  if [ -z "$dotenv_ssl" ] && [ -z "$shell_ssl" ] && [ "$yaml_ssl" = false ]; then
    assert_contains "$start_out" "DASHSCOPE_WORKSPACE_ID is empty" "$name: empty workspace warning"
  fi

  if [ -n "$shell_ssl" ]; then
    rc="$(run_rc "$status_out" "$d" status "LIVACHAT__SERVER__SSL__ENABLED=$shell_ssl")"
  else
    rc="$(run_rc "$status_out" "$d" status)"
  fi
  [ "$rc" = 0 ] || { cat "$status_out" >&2; fail "$name: status rc=$rc"; }
  assert_contains "$status_out" "livachat-server  running (healthy)" "$name: status healthy"
  assert_contains "$status_out" "$expected://" "$name: status display scheme"

  rc="$(run_rc "$d/stop.log" "$d" stop)"
  [ "$rc" = 0 ] || { cat "$d/stop.log" >&2; fail "$name: stop rc=$rc"; }
  ok "$name ($expected)"
}

test_scheme yaml-true true "" "" https
test_scheme yaml-false false "" "" http
test_scheme dotenv-false true false "" http
test_scheme shell-true false false true https

# Display address follows server.host; wildcard keeps the LAN-IP behavior (covered above), while
# loopback must not leak the LAN IP and explicit binds are shown as configured.
test_display_host() { # <name> <configured-host> <expected-host>
  local name="$1" configured="$2" expected="$3" d rc start_out status_out
  d="$(new_repo "$name" false "" "$configured")"
  start_out="$d/start.log"
  status_out="$d/status.log"
  rc="$(run_rc "$start_out" "$d" start)"
  [ "$rc" = 0 ] || { cat "$start_out" >&2; fail "$name: start rc=$rc"; }
  assert_contains "$start_out" "livachat-server ready: http://$expected:$SRV_PORT/healthz" "$name: ready display host"
  rc="$(run_rc "$status_out" "$d" status)"
  [ "$rc" = 0 ] || { cat "$status_out" >&2; fail "$name: status rc=$rc"; }
  assert_contains "$status_out" "http://$expected:$SRV_PORT/healthz" "$name: status display host"
  rc="$(run_rc "$d/stop.log" "$d" stop)"
  [ "$rc" = 0 ] || { cat "$d/stop.log" >&2; fail "$name: stop rc=$rc"; }
  ok "$name (display $expected)"
}

test_display_host host-loopback 127.0.0.1 127.0.0.1
test_display_host host-localhost localhost 127.0.0.1
test_display_host host-explicit 192.0.2.10 192.0.2.10
test_display_host host-ipv6 2001:db8::1 "[2001:db8::1]"

# Unreadable/malformed config: status must die before printing normal three-state output.
d="$(new_repo bad-config false)"
printf 'server: [\n' > "$d/configs/test/server.yaml"
rc="$(run_rc "$d/status.log" "$d" status)"
[ "$rc" = 1 ] || fail "bad config: expected rc=1, got $rc"
assert_contains "$d/status.log" "failed to load" "bad config: load error"
assert_contains "$d/status.log" "$d/configs/test/server.yaml" "bad config: path in error"
assert_contains "$d/status.log" "cannot determine server.ssl.enabled" "bad config: repair hint"
assert_not_contains "$d/status.log" "not running" "bad config: no three-state output"
ok "bad config dies"

# Missing venv python: status must die rather than silently falling back to https.
d="$(new_repo no-python false)"
rm -f "$d/.venv/bin/python"
rc="$(run_rc "$d/status.log" "$d" status)"
[ "$rc" = 1 ] || fail "no python: expected rc=1, got $rc"
assert_contains "$d/status.log" ".venv/bin/python not found" "no python: message"
ok "missing venv python dies"

# Variables resolved from ${...} must exist even when their values may be empty.
d="$(new_repo missing-presence false)"
grep -vE '^(DASHSCOPE_WORKSPACE_ID|HERMES_AUTH_TOKEN|OPENCLAW_AUTH_TOKEN)=' "$d/.env" > "$d/.env.new"
mv "$d/.env.new" "$d/.env"
rc="$(run_rc "$d/start.log" "$d" start)"
[ "$rc" = 1 ] || fail "missing presence: expected rc=1, got $rc"
assert_contains "$d/start.log" "DASHSCOPE_WORKSPACE_ID HERMES_AUTH_TOKEN OPENCLAW_AUTH_TOKEN" "missing presence: all names listed"
assert_contains "$d/start.log" "not defined" "missing presence: message"
[ ! -f "$d/.run/livachat.pid" ] || fail "missing presence: server was started"
ok "missing env lines die before start"

# Shell-provided empty values count as defined; start proceeds and only warns for workspace id.
d="$(new_repo shell-empty-presence false)"
grep -vE '^(DASHSCOPE_WORKSPACE_ID|HERMES_AUTH_TOKEN|OPENCLAW_AUTH_TOKEN)=' "$d/.env" > "$d/.env.new"
mv "$d/.env.new" "$d/.env"
rc="$(run_rc "$d/start.log" "$d" start \
  DASHSCOPE_WORKSPACE_ID= HERMES_AUTH_TOKEN= OPENCLAW_AUTH_TOKEN=)"
[ "$rc" = 0 ] || { cat "$d/start.log" >&2; fail "shell empty presence: start rc=$rc"; }
assert_contains "$d/start.log" "DASHSCOPE_WORKSPACE_ID is empty" "shell empty presence: warning"
rc="$(run_rc "$d/stop.log" "$d" stop)"
[ "$rc" = 0 ] || { cat "$d/stop.log" >&2; fail "shell empty presence: stop rc=$rc"; }
ok "shell empty values count as defined"

printf 'All livachat.sh start/status tests passed.\n'
