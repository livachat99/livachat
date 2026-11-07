#!/usr/bin/env bash

set -e

########################################
# Config
########################################

OPUS_VERSION=1.6.1

PROJECT_ROOT=$(cd "$(dirname "$0")/../.." && pwd)

OPUS_TAR="$PROJECT_ROOT/3rdparty/opus/opus-${OPUS_VERSION}.tar.gz"

SRC_DIR="$PROJECT_ROOT/3rdparty/build-android/opus-src"
BUILD_DIR="$PROJECT_ROOT/3rdparty/build-android/opus-build"
INSTALL_DIR="$PROJECT_ROOT/3rdparty/build-android/opus-install"

ANDROID_API=26

#ABIS=(
#  "arm64-v8a"
#  "armeabi-v7a"
#  "x86_64"
#)

ABIS=(
  "arm64-v8a"
  "x86_64"
)

########################################
# Check NDK
########################################

if [ -z "$ANDROID_NDK_HOME" ]; then
  echo "ERROR: ANDROID_NDK_HOME not set"
  exit 1
fi

echo "Using NDK:"
echo "$ANDROID_NDK_HOME"

########################################
# Prepare source
########################################

rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"

echo "Extracting opus..."

tar -xzf "$OPUS_TAR" -C "$SRC_DIR"

#OPUS_SRC=$(find "$SRC_DIR" -maxdepth 1 -type d -name "opus-*")
OPUS_SRC=$SRC_DIR/opus-1.6.1 

echo "Source:"
echo "$OPUS_SRC"

rm -rf $BUILD_DIR
rm -rf $INSTALL_DIR 

########################################
# Build function
########################################

build_one() {

  ABI=$1

  echo "==================================="
  echo "Building ABI: $ABI"
  echo "==================================="

  OUT_BUILD="$BUILD_DIR/$ABI"
  OUT_INSTALL="$INSTALL_DIR/$ABI"

  rm -rf "$OUT_BUILD"
  mkdir -p "$OUT_BUILD"

  cmake \
    -S "$OPUS_SRC" \
    -B "$OUT_BUILD" \
    -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$ABI" \
    -DANDROID_PLATFORM=android-${ANDROID_API} \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DOPUS_BUILD_TESTING=OFF \
    -DOPUS_BUILD_PROGRAMS=OFF \
    -DOPUS_STACK_PROTECTOR=ON \
    -DCMAKE_INSTALL_PREFIX="$OUT_INSTALL"

  cmake --build "$OUT_BUILD" -j$(sysctl -n hw.ncpu)

  cmake --install "$OUT_BUILD"

  echo ""
  echo "Installed:"
  echo "$OUT_INSTALL"
  echo ""
}

########################################
# Build all
########################################

for ABI in "${ABIS[@]}"; do
  build_one "$ABI"
done

echo ""
echo "==================================="
echo "DONE"
echo "==================================="
echo ""

echo "Output:"
echo "$INSTALL_DIR"

