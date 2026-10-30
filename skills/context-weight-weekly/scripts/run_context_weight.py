#!/usr/bin/env python3
"""Portable, history-isolated wrapper around context_weight.py.

Imports context_weight from a parameterized tools dir and runs one scan.
History policy: the scan writes history ONLY to an explicitly given
--history-path; with no --history-path, --no-history-write is required
(the canonical tools/context_weight.history.jsonl is never an implicit target).

Exit codes: 0 / 2 passed through from context_weight.main().
64 = usage error (bad args, missing/unloadable tools dir) - deliberately not 2,
which context_weight reserves for "hard fail or unmeasured".
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Sequence

EXIT_USAGE = 64
TOOLS_DIR_ENV = "CONTEXT_WEIGHT_TOOLS_DIR"
# Default: <repo>/tools, where this skill lives at <repo>/skills/context-weight-weekly/scripts/.
DEFAULT_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def _parser() -> _Parser:
    p = _Parser(prog="run_context_weight", description=__doc__.splitlines()[0])
    p.add_argument("--tools-dir", type=Path, default=None,
                   help=f"dir containing context_weight.py (default: ${TOOLS_DIR_ENV}, else <repo>/tools)")
    p.add_argument("--history-path", type=Path, default=None,
                   help="history JSONL to read trend from and append to (explicit only)")
    p.add_argument("--no-history-write", action="store_true",
                   help="do not append history (trend still read from --history-path if given)")
    return p


def resolve_tools_dir(arg: Optional[Path]) -> Path:
    if arg is not None:
        return arg
    env = os.environ.get(TOOLS_DIR_ENV)
    return Path(env) if env else DEFAULT_TOOLS_DIR


def load_context_weight(tools_dir: Path) -> ModuleType:
    src = tools_dir / "context_weight.py"
    if not src.is_file():
        raise UsageError(f"context_weight.py not found in tools dir: {tools_dir}")
    spec = importlib.util.spec_from_file_location("context_weight", src)
    if spec is None or spec.loader is None:
        raise UsageError(f"cannot load {src}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_weight"] = mod  # dataclasses resolve via sys.modules
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        sys.modules.pop("context_weight", None)
        raise UsageError(f"cannot import {src}: {e}") from e
    return mod


def run(argv: Optional[Sequence[str]] = None, **scan_overrides: Any) -> int:
    """Parse argv, run one scan, return its exit code. scan_overrides go to
    context_weight.main() (source-path overrides; used by fixture tests)."""
    try:
        args = _parser().parse_args(argv)
        if args.history_path is None and not args.no_history_write:
            raise UsageError("refusing implicit history write: pass --history-path PATH "
                             "or --no-history-write")
        cw = load_context_weight(resolve_tools_dir(args.tools_dir))
    except UsageError as e:
        print(f"run_context_weight: usage error: {e}", file=sys.stderr)
        return EXIT_USAGE

    overrides: dict[str, Any] = dict(scan_overrides)
    overrides["write_history"] = not args.no_history_write
    if args.history_path is not None:
        overrides["history_path"] = args.history_path
    else:
        # No explicit path: never touch the canonical file, not even to read trend.
        overrides["history_path"] = Path(os.devnull)
    rc = cw.main(**overrides)
    return int(rc)


if __name__ == "__main__":
    sys.exit(run())
