#!/usr/bin/env bash

set -e

########################################
# Config
########################################

OPUS_VERSION=1.6.1

PROJECT_ROOT=$(cd "$(dirname "$0")/../.." && pwd)

OPUS_TAR="$PROJECT_ROOT/3rdparty/opus/opus-${OPUS_VERSION}.tar.gz"

SRC_ROOT="$PROJECT_ROOT/3rdparty/build-ios/opus-src"
BUILD_ROOT="$PROJECT_ROOT/3rdparty/build-ios/opus-build"
OUTPUT_ROOT="$PROJECT_ROOT/3rdparty/build-ios/opus-install"

MIN_IOS_VERSION=13.0

########################################
# Check tools
########################################

command -v xcodebuild >/dev/null 2>&1 || {
  echo "ERROR: Xcode not installed"
  exit 1
}

command -v cmake >/dev/null 2>&1 || {
  echo "ERROR: cmake not installed"
  exit 1
}

echo "Using Xcode:"
xcodebuild -version

########################################
# Clean
########################################

rm -rf "$SRC_ROOT"
rm -rf "$BUILD_ROOT"
rm -rf "$OUTPUT_ROOT"

mkdir -p "$SRC_ROOT"
mkdir -p "$BUILD_ROOT"
mkdir -p "$OUTPUT_ROOT"

########################################
# Extract source
########################################

echo "Extracting opus..."

tar -xzf "$OPUS_TAR" -C "$SRC_ROOT"

#OPUS_SRC=$(find "$SRC_ROOT" -maxdepth 1 -type d -name "opus-*")
OPUS_SRC=$SRC_ROOT/opus-$OPUS_VERSION

echo "Source:"
echo "$OPUS_SRC"

########################################
# Build function
########################################

build_one() {

  PLATFORM=$1
  ARCHS=$2
  SDK=$3
  NAME=$4

  echo ""
  echo "===================================="
  echo "Building:"
  echo "Platform : $PLATFORM"
  echo "Archs    : $ARCHS"
  echo "SDK      : $SDK"
  echo "===================================="
  echo ""

  BUILD_DIR="$BUILD_ROOT/$NAME"
  INSTALL_DIR="$OUTPUT_ROOT/$NAME"

  mkdir -p "$BUILD_DIR"

  cmake \
    -S "$OPUS_SRC" \
    -B "$BUILD_DIR" \
    -G Xcode \
    -DCMAKE_SYSTEM_NAME=iOS \
    -DCMAKE_OSX_ARCHITECTURES="$ARCHS" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_IOS_VERSION" \
    -DCMAKE_OSX_SYSROOT="$SDK" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DOPUS_BUILD_TESTING=OFF \
    -DOPUS_BUILD_PROGRAMS=OFF \
    -DCMAKE_INSTALL_PREFIX="$INSTALL_DIR"

  cmake --build "$BUILD_DIR" \
    --config Release

  cmake --install "$BUILD_DIR" \
    --config Release

  echo ""
  echo "Installed:"
  echo "$INSTALL_DIR"
}

########################################
# Device
########################################

build_one \
  "iPhoneOS" \
  "arm64" \
  "iphoneos" \
  "ios-device"

########################################
# Simulator
########################################

build_one \
  "iPhoneSimulator" \
  "arm64;x86_64" \
  "iphonesimulator" \
  "ios-simulator"

########################################
# Create XCFramework
########################################

echo ""
echo "===================================="
echo "Creating XCFramework"
echo "===================================="
echo ""

DEVICE_LIB="$OUTPUT_ROOT/ios-device/lib/libopus.a"
SIM_LIB="$OUTPUT_ROOT/ios-simulator/lib/libopus.a"

DEVICE_HEADERS="$OUTPUT_ROOT/ios-device/include"
SIM_HEADERS="$OUTPUT_ROOT/ios-simulator/include"

XCFRAMEWORK_OUT="$OUTPUT_ROOT/Opus.xcframework"

xcodebuild -create-xcframework \
  -library "$DEVICE_LIB" \
  -headers "$DEVICE_HEADERS" \
  -library "$SIM_LIB" \
  -headers "$SIM_HEADERS" \
  -output "$XCFRAMEWORK_OUT"

echo ""
echo "===================================="
echo "DONE"
echo "===================================="
echo ""

echo "XCFramework:"
echo "$XCFRAMEWORK_OUT"

