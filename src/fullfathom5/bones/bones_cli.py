#!/usr/bin/env python3
"""
Bones CLI — thin entrypoint that wires args/env → BonesRepl/ChoralRepl.

Usage examples:
  bones                              # plain REPL
  bones --ui choral                  # try full-screen UI (if available)
  bones --model gpt-4o-mini          # override model
  bones --rate 0.7 --max-tokens 1800 # tune runtime params
  bones --version                    # print version
"""

from __future__ import annotations

import os
import sys
import argparse
import asyncio
import importlib.metadata as _ilm
from typing import Optional

from .repl_base import BonesRepl  # core REPL (always available)

# Optional Choral UI (full-screen). Fallback to BonesRepl if unavailable.
try:
    from .choral_base import ChoralRepl  # type: ignore
    _HAS_CHORAL = True
except Exception:
    ChoralRepl = None  # type: ignore
    _HAS_CHORAL = False


# ---------- helpers ----------
def _pkg_version() -> str:
    try:
        return _ilm.version("fullfathom5")
    except Exception:
        return "0+unknown"


def _choose_model(cli_model: Optional[str]) -> str:
    """Pick a model: CLI > env (BONES_MODEL/OPENAI_MODEL) > default."""
    if cli_model and cli_model.strip():
        return cli_model.strip()
    for key in ("BONES_MODEL", "OPENAI_MODEL"):
        v = os.getenv(key, "").strip()
        if v:
            return v
    return "gpt-4o-mini"


# ---------- entry ----------
def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Bones CLI — a meta tool for code + papers")
    p.add_argument("--model", default=None, help="Override model (or BONES_MODEL / OPENAI_MODEL env)")
    p.add_argument("--rate", default=0.5, type=float, help="Requests/sec budget")
    p.add_argument("--max-tokens", default=1200, type=int, dest="max_tokens", help="Max response tokens")
    p.add_argument("--ui", choices=["repl", "choral"], default="repl",
                   help="Choose interface: 'repl' (default) or 'choral' (full-screen if available)")
    p.add_argument("--version", action="version", version=f"Bones {_pkg_version()}")
    args = p.parse_args(argv)

    model = _choose_model(args.model)

    # Select UI class
    if args.ui == "choral":
        if _HAS_CHORAL and ChoralRepl is not None:
            ReplClass = ChoralRepl
        else:
            print("WARN: Choral UI not available; falling back to plain REPL.", file=sys.stderr)
            ReplClass = BonesRepl
    else:
        ReplClass = BonesRepl

    repl = ReplClass(model=model, rate=args.rate, max_tokens=args.max_tokens)

    try:
        asyncio.run(repl.run())
    except RuntimeError as e:
        # Some environments already run a loop (e.g., IPython). Fall back gracefully.
        if "asyncio.run() cannot be called" in str(e):
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(repl.run())
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        else:
            raise


if __name__ == "__main__":
    main()
