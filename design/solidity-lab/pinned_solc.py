"""Fail-closed access to the lab's pinned Solidity compiler (review A3).

The pinned solc belongs to the lab's reproducibility contract: a pinned source
commit plus this exact compiler reproduce the generated artifacts
byte-for-byte. Two failure classes must never be conflated:

- ``CompilerUnavailable`` — the tool itself could not be run (no npx, no
  network, timeout). Where the suites are optional (a local checkout) this may
  skip, explicitly labelled; wherever they are mandatory gates (CI, release
  checks that export ``CI=1``) it is a hard error.
- ``CompileFailed`` — the tool ran and rejected the source, or wrote no
  artifacts. Always a hard failure: a compiler error must never become a
  green skip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

SOLC_VERSION = "0.8.37"
TIMEOUT_SECONDS = 300


class CompilerUnavailable(Exception):
    """The pinned compiler tool itself could not be run."""


class CompileFailed(Exception):
    """The pinned compiler ran and rejected the source or wrote no artifacts."""


def tools_mandatory() -> bool:
    """True wherever the pinned toolchain is a required gate (CI sets CI=true)."""
    return bool(os.environ.get("CI"))


def _solc_invocation() -> list[str]:
    npx = shutil.which("npx")
    if npx is None:
        raise CompilerUnavailable("npx not found on PATH")
    return [npx, "--yes", f"solc@{SOLC_VERSION}"]


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired as error:
        raise CompilerUnavailable(
            f"pinned solc {SOLC_VERSION} did not answer within {TIMEOUT_SECONDS}s") from error
    except OSError as error:
        raise CompilerUnavailable(f"could not run the pinned compiler: {error}") from error


def probe() -> None:
    """Prove the pinned compiler runs at all, independent of any source."""
    result = _run(_solc_invocation() + ["--version"])
    if result.returncode != 0 or SOLC_VERSION not in result.stdout:
        detail = (result.stderr or result.stdout).strip()[:200]
        raise CompilerUnavailable(f"pinned solc {SOLC_VERSION} did not run: {detail}")


def enforce_availability() -> None:
    """Probe the tool and apply the availability policy.

    Raises ``unittest.SkipTest`` where the suites are optional (explicit,
    labelled skip) and ``AssertionError`` wherever the toolchain is a
    mandatory gate (CI, release checks exporting CI=1).
    """
    try:
        probe()
    except CompilerUnavailable as error:
        if tools_mandatory():
            raise AssertionError(f"pinned solc is a mandatory gate in CI but unavailable: {error}") from error
        raise unittest.SkipTest(
            f"pinned solc unavailable in this environment (optional locally): {error}") from error


def compile_with_artifacts(source: Path, workdir: Path, stem: str) -> dict[str, bytes]:
    """Compile ``source`` with the pinned solc into ``workdir``.

    Returns ``{"bin": ..., "abi": ...}``; every failure mode raises
    ``CompileFailed`` so callers can never mistake it for a skip.
    """
    result = _run(_solc_invocation() + ["--bin", "--abi", str(source)], cwd=workdir)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:400]
        raise CompileFailed(f"pinned solc {SOLC_VERSION} rejected {source.name}: {detail}")
    binaries = sorted(workdir.glob(f"*_{stem}.bin"))
    abis = sorted(workdir.glob(f"*_{stem}.abi"))
    if not binaries or not abis:
        raise CompileFailed(
            f"pinned solc wrote no artifacts for {stem}: {result.stdout.strip()[:200]}")
    return {"bin": binaries[0].read_bytes(), "abi": abis[0].read_bytes()}
