cmake_minimum_required(VERSION 3.22.1)
project("livachat_native")

set(THIRDPARTY_ROOT "${CMAKE_SOURCE_DIR}/../../../3rdparty/dist-android/${ANDROID_ABI}")

# ── Prebuilt Opus ──────────────────────────────────────────────────
add_library(opus STATIC IMPORTED)
set_target_properties(opus PROPERTIES
    IMPORTED_LOCATION "${THIRDPARTY_ROOT}/lib/libopus.a"
)
target_include_directories(opus INTERFACE "${THIRDPARTY_ROOT}/include/opus")

# ── Prebuilt WebRTC VAD Lite ───────────────────────────────────────
add_library(webrtc_vad_lite STATIC IMPORTED)
set_target_properties(webrtc_vad_lite PROPERTIES
    IMPORTED_LOCATION "${THIRDPARTY_ROOT}/lib/libwebrtc_vad_lite.a"
)
target_include_directories(webrtc_vad_lite INTERFACE "${THIRDPARTY_ROOT}/include")

# ── Shared library ─────────────────────────────────────────────────
add_library(livachat_native SHARED
    src/main/cpp/livachat_native.cpp
    src/main/cpp/opus_bridge.cpp
    src/main/cpp/vad_bridge.cpp
)

target_include_directories(livachat_native PRIVATE
    "${THIRDPARTY_ROOT}/include"
    "${THIRDPARTY_ROOT}/include/opus"
)

target_link_libraries(livachat_native
    opus
    webrtc_vad_lite
    log
)
