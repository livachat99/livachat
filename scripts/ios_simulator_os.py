#!/usr/bin/env python3
"""Print the newest iOS runtime version that has the given simulator.

`xcodebuild -destination 'platform=iOS Simulator,name=X'` resolves the name
against the newest installed runtime, which fails when X only exists on an
older runtime. Pinning `OS=` to a version that actually has the device keeps
`make ios-app-test` / `make ios-app-uitest` working on machines with several
runtimes installed.

Only runtimes supported by the active Xcode are considered: CI images can
carry newer simulator runtimes than the selected Xcode's SDK, and picking one
of those would make xcodebuild fail to use the destination.

Prints an empty string when no matching device is found.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

RUNTIME_VERSION = re.compile(r"iOS-(\d+)-(\d+)")
SDK_VERSION = re.compile(r"^(\d+)\.(\d+)")


def run(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def active_sdk_version() -> tuple[int, int] | None:
    output = run(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"])
    match = SDK_VERSION.match((output or "").strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    output = run(["xcrun", "simctl", "list", "devices", "available", "-j"])
    if output is None:
        return 0
    try:
        devices = json.loads(output).get("devices", {})
    except json.JSONDecodeError:
        return 0

    sdk = active_sdk_version()
    best: tuple[int, int] | None = None
    for runtime, entries in devices.items():
        match = RUNTIME_VERSION.search(runtime)
        if not match or name not in {entry.get("name") for entry in entries}:
            continue
        version = (int(match.group(1)), int(match.group(2)))
        if sdk is not None and version > sdk:
            continue
        if best is None or version > best:
            best = version

    if best is not None:
        print(f"{best[0]}.{best[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
