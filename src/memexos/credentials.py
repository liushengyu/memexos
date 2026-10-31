"""Secrets come from the environment or the operating system's keychain, never from files we write.

A config value such as `api_key_env = "ANTHROPIC_API_KEY"` names where the
secret lives. Lookup order:
  1. the environment variable itself;
  2. macOS Keychain (generic password, service "memexos", account = the name);
  3. Linux Secret Service via `secret-tool` (attributes service=memexos key=<name>).
Store one with `memex secret set <NAME>`.
"""

from __future__ import annotations

import os
import platform
import subprocess

from memexos.util import which

SERVICE = "memexos"


def get(name: str | None) -> str | None:
    if not name:
        return None
    v = os.environ.get(name)
    if v:
        return v.strip()
    try:
        if platform.system() == "Darwin" and which("security"):
            r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                               capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        elif which("secret-tool"):
            r = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "key", name],
                               capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def put(name: str, value: str) -> str:
    """Store a secret in the OS keychain. Returns where it went."""
    if platform.system() == "Darwin":
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)
        return "macOS Keychain"
    if which("secret-tool"):
        subprocess.run(["secret-tool", "store", "--label", f"{SERVICE} {name}", "service", SERVICE, "key", name],
                       input=value, text=True, check=True, capture_output=True)
        return "Secret Service"
    raise RuntimeError("no keychain available: set the environment variable instead")


def available(name: str | None) -> bool:
    return bool(get(name))
