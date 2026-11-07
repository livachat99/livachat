#!/usr/bin/env bash
#
# iOS build entry point.
#
# Usage:
#   ./scripts/build-ios.sh [sdk|app|all]   (default: sdk)
#
# sdk: generated DTO drift check + protocol/SDK tests.
#      RUN_IOS_TESTS=0 skips iOS simulator tests.
# app: signed LivaChatApp build for the device in IOS_DEVICE_UDID.
#      Must run from a GUI Terminal/Xcode session: codesign needs keychain access.
#      Env required: IOS_DEVICE_UDID, IOS_DEVELOPMENT_TEAM; optional: IOS_DERIVED_DATA_PATH.
# all: sdk, then app.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: ./scripts/build-ios.sh [sdk|app|all]

  sdk   Generated DTO check + protocol/SDK tests (default)
        RUN_IOS_TESTS=0 skips iOS simulator tests.
  app   Signed LivaChatApp build for the device in IOS_DEVICE_UDID.
        Must run from a GUI Terminal/Xcode session.
        Required env: IOS_DEVICE_UDID, IOS_DEVELOPMENT_TEAM.
        Optional env: IOS_DERIVED_DATA_PATH.
  all   sdk, then app.
EOF
}

require_xcframework() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    echo "ERROR: $path is missing" >&2
    exit 1
  fi
}

build_sdk() {
  require_xcframework 3rdparty/dist-ios/Opus.xcframework
  require_xcframework 3rdparty/dist-ios/WebRtcVadLite.xcframework

  echo "==> Checking generated Swift protocol DTOs"
  make check-swift-api-generated

  echo "==> Protocol DTO tests"
  make swift-protocol-test

  echo "==> Host-side SDK tests"
  make swift-sdk-test

  if [[ "${RUN_IOS_TESTS:-1}" == "1" ]]; then
    echo "==> iOS simulator tests"
    make swift-sdk-ios-test
  else
    echo "==> Skipping iOS simulator tests (RUN_IOS_TESTS=0)"
  fi

  echo "iOS SDK build/test OK"
}

build_app() {
  require_xcframework 3rdparty/dist-ios/Opus.xcframework
  require_xcframework 3rdparty/dist-ios/WebRtcVadLite.xcframework

  local device_udid="${IOS_DEVICE_UDID:-}"
  local team_id="${IOS_DEVELOPMENT_TEAM:-}"
  local derived_data="${IOS_DERIVED_DATA_PATH:-apps/ios/LivaChatApp/build/device-signed}"

  if [[ -z "$device_udid" || -z "$team_id" ]]; then
    echo "ERROR: IOS_DEVICE_UDID and IOS_DEVELOPMENT_TEAM are required for app builds" >&2
    exit 1
  fi

  # A non-GUI shell cannot access the login keychain; codesign would fail with
  # errSecInternalComponent. Warn early instead of failing at the end of the build.
  if ! security show-keychain-info "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1; then
    echo "WARNING: keychain is not accessible from this shell; run from GUI Terminal/Xcode or codesign may fail." >&2
  fi

  echo "==> Building LivaChatApp for device ${device_udid} (team ${team_id})"
  xcodebuild build \
      -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
      -scheme LivaChatApp \
      -configuration Debug \
      -derivedDataPath "$derived_data" \
      -destination "platform=iOS,id=${device_udid}" \
      DEVELOPMENT_TEAM="$team_id" \
      -allowProvisioningUpdates
  echo "iOS app build OK"
}

MODE="${1:-sdk}"
case "$MODE" in
  sdk) build_sdk ;;
  app) build_app ;;
  all) build_sdk; build_app ;;
  -h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
