#!/usr/bin/env bash
# Isolated tests for `livachat.sh install --livekit`.
# No real network or Homebrew install is used: a fake livekit-server and local
# file:// release/raw roots are injected through LIVEKIT_SERVER_BIN,
# LIVEKIT_RELEASE_BASE and LIVEKIT_RAW_BASE.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIVACHAT="$REPO_ROOT/livachat.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/livachat-livekit-test.XXXXXX")"
trap 'rm -rf "$T"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
info() { printf 'ok: %s\n' "$*"; }
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}
perm() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"; }

mkdir -p "$T/raw/v1.13.11" "$T/bin" "$T/release/v1.13.11"
printf 'port: 7880\n' > "$T/raw/v1.13.11/config-sample.yaml"
printf 'Apache License 2.0\n' > "$T/raw/v1.13.11/LICENSE"

cat > "$T/fake-livekit-server" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "livekit-server version 1.13.11" ;;
  generate-keys) printf 'API Key:  APIabcdef123456\nAPI Secret:  secretabcdefghijklmnop\n' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$T/fake-livekit-server"

export LIVEKIT_SERVER_BIN="$T/fake-livekit-server"
export LIVEKIT_RAW_BASE="file://$T/raw"

new_repo() {
  local d="$T/$1"
  mkdir -p "$d"
  cp "$LIVACHAT" "$d/livachat.sh"
  chmod +x "$d/livachat.sh"
  printf '%s\n' "$d"
}

# 1. First install with an existing pair + URL: replace only key/secret.
R="$(new_repo first)"
cat > "$R/.env" <<'EOF'
APP_AUTH_TOKEN=keep-me
LIVACHAT__MEETING__LIVEKIT_KEY=oldkey
LIVACHAT__MEETING__LIVEKIT_SECRET=oldsecret
LIVACHAT__MEETING__LIVEKIT_SERVER=wss://old.example:443
EOF
chmod 600 "$R/.env"
(cd "$R" && ./livachat.sh install --livekit --skip-base >out.log 2>&1)
grep -q '^LIVACHAT__MEETING__LIVEKIT_KEY=APIabcdef123456$' "$R/.env" || fail "new key not written"
grep -q '^LIVACHAT__MEETING__LIVEKIT_SECRET=secretabcdefghijklmnop$' "$R/.env" || fail "new secret not written"
grep -q '^LIVACHAT__MEETING__LIVEKIT_SERVER=wss://old.example:443$' "$R/.env" || fail "URL changed"
grep -q '^APP_AUTH_TOKEN=keep-me$' "$R/.env" || fail "unrelated env changed"
[ -f "$R/bin/livekit/livekit.yaml" ] || fail "config missing"
[ -f "$R/bin/livekit/config-sample.yaml" ] || fail "sample missing"
[ -f "$R/bin/livekit/LICENSE" ] || fail "LICENSE missing"
ls "$R"/.env.bak.* >/dev/null 2>&1 || fail "env backup missing"
[ "$(perm "$R/.env")" = 600 ] || fail "env mode"
[ "$(perm "$R/bin/livekit/livekit.yaml")" = 600 ] || fail "config mode"
[ "$(perm "$R/bin/livekit/config-sample.yaml")" = 644 ] || fail "sample mode"
info "first install"

# 2. Rerun with existing config must not modify key/env/config.
h_env="$(sha256_file "$R/.env")"
h_cfg="$(sha256_file "$R/bin/livekit/livekit.yaml")"
(cd "$R" && ./livachat.sh install --livekit --skip-base >out2.log 2>&1)
[ "$h_env" = "$(sha256_file "$R/.env")" ] || fail "rerun changed .env"
[ "$h_cfg" = "$(sha256_file "$R/bin/livekit/livekit.yaml")" ] || fail "rerun changed config"
info "rerun is read-only"

# 3. Dry run must not write.
R="$(new_repo dry)"
(cd "$R" && ./livachat.sh install --livekit --skip-base --dry-run >out.log 2>&1)
grep -q 'First install' "$R/out.log" || fail "dry-run plan missing"
[ ! -e "$R/bin" ] || fail "dry-run created bin"
[ ! -e "$R/.env" ] || fail "dry-run created .env"
info "dry run"

# 4. Shell key/secret override on first install fails before key writes.
R="$(new_repo shell-key)"
set +e
(cd "$R" && LIVACHAT__MEETING__LIVEKIT_KEY=x ./livachat.sh install --livekit --skip-base >out.log 2>&1)
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "shell override should fail"
grep -qi 'unset before first install' "$R/out.log" || fail "shell override message"
[ ! -e "$R/bin/livekit/livekit.yaml" ] || fail "shell override wrote config"
[ ! -e "$R/.env" ] || fail "shell override wrote env"
info "shell override"

# 5. No .env: create one and write the pair.
R="$(new_repo no-env)"
(cd "$R" && ./livachat.sh install --livekit --skip-base >out.log 2>&1)
[ -f "$R/.env" ] || fail "env not created"
grep -q '^LIVACHAT__MEETING__LIVEKIT_KEY=APIabcdef123456$' "$R/.env" || fail "no-env key"
info "no .env"

# 6. Existing config with mismatched .env: warn, do not modify.
R="$(new_repo mismatch)"
(cd "$R" && ./livachat.sh install --livekit --skip-base >/dev/null 2>&1)
awk '{ if ($0 ~ /^LIVACHAT__MEETING__LIVEKIT_KEY=/) print "LIVACHAT__MEETING__LIVEKIT_KEY=changed"; else print }' \
  "$R/.env" > "$R/.env.new"
mv "$R/.env.new" "$R/.env"
chmod 600 "$R/.env"
h_env="$(sha256_file "$R/.env")"
(cd "$R" && ./livachat.sh install --livekit --skip-base >out.log 2>&1)
grep -qi 'does not match livekit.yaml' "$R/out.log" || fail "mismatch warning missing"
[ "$h_env" = "$(sha256_file "$R/.env")" ] || fail "mismatch modified .env"
info "read-only mismatch warning"

# 7. Linux release path: download, checksum, extract.
cat > "$T/bin/uname" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  -s) echo Linux ;;
  -m) echo x86_64 ;;
  *) /usr/bin/uname "$@" ;;
esac
EOF
chmod +x "$T/bin/uname"
mkdir -p "$T/pkg"
cp "$T/fake-livekit-server" "$T/pkg/livekit-server"
printf 'Apache License 2.0\n' > "$T/pkg/LICENSE"
tar -czf "$T/release/v1.13.11/livekit_1.13.11_linux_amd64.tar.gz" -C "$T/pkg" livekit-server LICENSE
h="$(sha256_file "$T/release/v1.13.11/livekit_1.13.11_linux_amd64.tar.gz")"
printf '%s  livekit_1.13.11_linux_amd64.tar.gz\n' "$h" > "$T/release/v1.13.11/checksums.txt"
R="$(new_repo linux)"
(cd "$R" && env -u LIVEKIT_SERVER_BIN PATH="$T/bin:$PATH" \
  LIVEKIT_RELEASE_BASE="file://$T/release" LIVEKIT_RAW_BASE="file://$T/raw" \
  ./livachat.sh install --livekit --skip-base >out.log 2>&1)
[ -f "$R/bin/livekit/livekit.yaml" ] || fail "linux config missing"
[ -f "$R/bin/livekit/checksums.txt" ] || fail "linux checksums missing"
[ "$(cat "$R/bin/livekit/VERSION")" = 1.13.11 ] || fail "linux VERSION"
info "linux release path"

# 8. Checksum mismatch: fail without config/env.
R="$(new_repo checksum)"
printf 'deadbeef  livekit_1.13.11_linux_amd64.tar.gz\n' > "$T/release/v1.13.11/checksums.txt"
set +e
(cd "$R" && env -u LIVEKIT_SERVER_BIN PATH="$T/bin:$PATH" \
  LIVEKIT_RELEASE_BASE="file://$T/release" LIVEKIT_RAW_BASE="file://$T/raw" \
  ./livachat.sh install --livekit --skip-base >out.log 2>&1)
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "checksum mismatch should fail"
grep -q 'checksum mismatch' "$R/out.log" || fail "checksum message"
[ ! -e "$R/bin/livekit/livekit.yaml" ] || fail "checksum wrote config"
[ ! -e "$R/.env" ] || fail "checksum wrote env"
info "checksum mismatch"

# 9. .env symlink: fail, do not replace the link.
R="$(new_repo symlink)"
printf 'APP_AUTH_TOKEN=x\n' > "$T/real-env"
ln -s "$T/real-env" "$R/.env"
set +e
(cd "$R" && ./livachat.sh install --livekit --skip-base >out.log 2>&1)
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "symlink .env should fail"
grep -q 'is a symlink' "$R/out.log" || fail "symlink message"
[ -L "$R/.env" ] || fail "symlink replaced"
info "symlink .env"

# 10. macOS Homebrew copy path (fake brew; skipped on non-Darwin).
if [ "$(uname -s)" = "Darwin" ]; then
  mkdir -p "$T/fakebin" "$T/brewprefix/bin"
  cat > "$T/brewprefix/bin/livekit-server" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "livekit-server version 1.13.11" ;;
  generate-keys) printf 'API Key:  APIbrewkey12345\nAPI Secret:  secretbrew12345zz\n' ;;
  *) exit 1 ;;
esac
EOF
  chmod +x "$T/brewprefix/bin/livekit-server"
  cat > "$T/fakebin/brew" <<EOF
#!/usr/bin/env bash
case "\${1:-}" in
  list) exit 0 ;;
  --prefix) echo "$T/brewprefix" ;;
  install) exit 0 ;;
  *) exit 1 ;;
esac
EOF
  chmod +x "$T/fakebin/brew"
  R="$(new_repo macos)"
  (cd "$R" && env -u LIVEKIT_SERVER_BIN PATH="$T/fakebin:$PATH" \
    LIVEKIT_RAW_BASE="file://$T/raw" ./livachat.sh install --livekit --skip-base >out.log 2>&1)
  [ -x "$R/bin/livekit/livekit-server" ] || fail "macOS binary missing"
  [ "$(cat "$R/bin/livekit/VERSION")" = 1.13.11 ] || fail "macOS VERSION"
  grep -q '^LIVACHAT__MEETING__LIVEKIT_KEY=APIbrewkey12345$' "$R/.env" || fail "macOS key"
  info "macOS Homebrew path"
fi

# 11. Version prefix must not match a longer patch version (1.13.1 != 1.13.11).
R="$(new_repo version-prefix)"
(cd "$R" && ./livachat.sh install --livekit --skip-base --livekit-version 1.13.1 >out.log 2>&1)
grep -q 'differs from requested 1.13.1' "$R/out.log" || fail "version prefix collision not detected"
[ "$(cat "$R/bin/livekit/VERSION")" = 1.13.11 ] || fail "version prefix VERSION"
info "version exactness"

# 12. Argument errors.
R="$(new_repo args)"
set +e
(cd "$R" && ./livachat.sh install --livekit --skip-base --livekit-version 1.2 >v.log 2>&1)
rc_v=$?
(cd "$R" && ./livachat.sh install --skip-base >s.log 2>&1)
rc_s=$?
(cd "$R" && ./livachat.sh install --bogus >u.log 2>&1)
rc_u=$?
set -e
[ "$rc_v" -ne 0 ] || fail "invalid version accepted"
[ "$rc_s" -ne 0 ] || fail "--skip-base alone accepted"
[ "$rc_u" -ne 0 ] || fail "unknown option accepted"
grep -q 'invalid LiveKit version' "$R/v.log" || fail "invalid version message"
grep -q 'requires --livekit' "$R/s.log" || fail "skip-base message"
grep -q 'unknown install option' "$R/u.log" || fail "unknown option message"
info "argument errors"

printf 'All LiveKit install tests passed.\n'
