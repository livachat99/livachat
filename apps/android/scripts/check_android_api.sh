#!/usr/bin/env bash
# LivaChat Android SDK — Public API surface checker
#
# Compares the public API of LivaChatSDK against the expected contract
# defined in the v2 design doc §2.1–2.3.
#
# Usage: bash scripts/check_android_api.sh

set -euo pipefail

SDK_DIR="$(cd "$(dirname "$0")/../LivaChatSDK" && pwd)"
SRC_DIR="$SDK_DIR/src/main/kotlin/dev/livachat/sdk"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

errors=0

check_class() {
    local file="$1"
    local name="$2"
    if [ -f "$SRC_DIR/$file" ]; then
        echo -e "${GREEN}✓${NC} $name ($file)"
    else
        echo -e "${RED}✗${NC} $name — missing $file"
        errors=$((errors + 1))
    fi
}

check_method() {
    local file="$1"
    local pattern="$2"
    local desc="$3"
    if grep -q "$pattern" "$SRC_DIR/$file" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} $desc"
    else
        echo -e "${RED}✗${NC} $desc — not found in $file"
        errors=$((errors + 1))
    fi
}

echo "=== LivaChatSDK Public API Surface Check ==="
echo ""

# ── §2.1 Core entry point ──────────────────────────────────────────
check_class "LivaChatSDK.kt" "LivaChatSDK"
check_method "LivaChatSDK.kt" "fun connect()" "connect()"
check_method "LivaChatSDK.kt" "fun disconnect()" "disconnect()"
check_method "LivaChatSDK.kt" "fun startSession" "startSession(resumeSessionId?)"
check_method "LivaChatSDK.kt" "fun endSession" "endSession(reason?)"
check_method "LivaChatSDK.kt" "fun pushAudioPcm" "pushAudioPcm(pcm, offset, len, muted?)"
check_method "LivaChatSDK.kt" "fun pushVideoBitmap" "pushVideoBitmap(bitmap, orientation, quality)"
check_method "LivaChatSDK.kt" "fun sendInterrupt" "sendInterrupt(reason?)"
check_method "LivaChatSDK.kt" "val connectionState" "connectionState: StateFlow<ConnectionState>"
check_method "LivaChatSDK.kt" "val sessionState" "sessionState: StateFlow<SessionState>"
check_method "LivaChatSDK.kt" "val bandwidthHint" "bandwidthHint: StateFlow<BandwidthHint>"
check_method "LivaChatSDK.kt" "val localVadState" "localVadState: StateFlow<VadState>"

# ── §2.2 Config ────────────────────────────────────────────────────
check_class "LivaChatConfig.kt" "LivaChatConfig"
check_method "LivaChatConfig.kt" "val serverUrl" "serverUrl: String"
check_method "LivaChatConfig.kt" "val token" "token: String"
check_method "LivaChatConfig.kt" "val deviceId" "deviceId: String"
check_method "LivaChatConfig.kt" "val clientVersion" "clientVersion: String"
check_method "LivaChatConfig.kt" "val audioSampleRate" "audioSampleRate: AudioSampleRate"

# ── §2.3 Listener ──────────────────────────────────────────────────
check_class "LivaChatListener.kt" "LivaChatListener"
check_method "LivaChatListener.kt" "onConnectionStateChanged" "onConnectionStateChanged(state)"
check_method "LivaChatListener.kt" "onSessionStateChanged" "onSessionStateChanged(state)"
check_method "LivaChatListener.kt" "onSessionReady" "onSessionReady(sessionId, restored)"
check_method "LivaChatListener.kt" "onSessionEnded" "onSessionEnded(reason, finalSessionId)"
check_method "LivaChatListener.kt" "onTranscription" "onTranscription(text, isFinal, transcriptionId)"
check_method "LivaChatListener.kt" "onAiText" "onAiText(text, messageId, index, isFinal)"
check_method "LivaChatListener.kt" "onAiAudioPcm" "onAiAudioPcm(pcm, sampleRate, durationMs, isFinal, chunkSeq)"
check_method "LivaChatListener.kt" "onPlaybackShouldFlush" "onPlaybackShouldFlush(reason)"
check_method "LivaChatListener.kt" "onVideoAck" "onVideoAck(frameId, processed)"
check_method "LivaChatListener.kt" "onError" "onError(code, message, fatal, recoverable)"

# ── Model enums ────────────────────────────────────────────────────
for enum in ConnectionState SessionState VadState Orientation BandwidthHint PlaybackFlushReason InterruptReason SessionEndReason AudioSampleRate VadSensitivity; do
    check_class "model/${enum}.kt" "$enum"
done

echo ""
if [ $errors -eq 0 ]; then
    echo -e "${GREEN}All API surface checks passed.${NC}"
    exit 0
else
    echo -e "${RED}$errors API surface check(s) failed.${NC}"
    exit 1
fi
