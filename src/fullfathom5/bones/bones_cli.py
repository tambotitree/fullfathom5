#!/usr/bin/env python3
"""
Bones CLI — MVP REPL with vim-style :commands.
Relies on eqnlint.lib._ai.AIClient for model calls.
"""

from __future__ import annotations
import sys, os, re, argparse, asyncio, textwrap
from typing import Optional

from eqnlint.lib._ai import AIClient  # single source of truth
from .commands import CommandProcessor, CommandAction
from .state_machine import StateMachine


def _small_talk_fastpath(s: str) -> Optional[str]:
    """Return a canned response (or empty string to no-op) if input is trivial."""
    t = s.strip().lower()
    if not t:
        return ""
    # exact short greetings
    if t in {"hi", "hello", "yo", "hey", "thanks", "thank you", "sup", "hola"}:
        return "👋 Hi! What do you want to do in this repo?"
    # soft patterns like 'say hello', 'say hi', 'greet'
    if re.fullmatch(r"\s*(please\s+)?(say\s+)?(hi|hello)\s*[.!]?\s*", t):
        return "👋 Hello! Ready when you are."
    if re.fullmatch(r"\s*(please\s+)?greet\s*(me)?\s*[.!]?\s*", t):
        return "👋 Hello! How can I help in this codebase?"
    return None

def _choose_model(cli_model: Optional[str]) -> str:
    """Pick a model: CLI > env > default."""
    if cli_model and cli_model.strip():
        return cli_model.strip()
    for key in ("BONES_MODEL", "OPENAI_MODEL"):
        v = os.getenv(key, "").strip()
        if v:
            return v
    return "gpt-4o-mini"

async def _repl(model: str, rate: float, max_tokens: int) -> None:
    # Create the shared AI client via eqnlint
    ai = AIClient(model=model, rate=rate, max_tokens=max_tokens)
    sm = StateMachine(ai)                 # orchestrates SELECT→CONTEXT→SOLVE
    cp = CommandProcessor(ai, sm)         # handles :commands
    print("bones chat — type your request (Ctrl-C to exit)")
    print("tips: ':q' quit, ':w' apply writes, ':m <model>', ':r <rate>', ':tokens <n>', '::text' to send leading colon.")

    try:
        while True:
            line = input("\nYou> ").rstrip("\n")

            # 1) Command prefix
            if line.startswith(":"):
                # "::foo" = escape colon → send ":foo" to model
                if line.startswith("::"):
                    line = line[1:]
                else:
                    action = cp.handle(line)
                    if action is CommandAction.QUIT:
                        print("Exiting chat.")
                        break
                    # Other actions already printed feedback / mutated session
                    continue

            # 2) Small-talk fast path
            fast = _small_talk_fastpath(line)
            if fast is not None:
                if fast:
                    print(fast)
                continue

            # 3) Normal state-machine turn
            outcome = await sm.run_turn(line)
            if "answer_md" in outcome:
                print(textwrap.dedent(outcome["answer_md"]).strip())
            elif "writes" in outcome:
                print("Proposed writes (staged). Use ':w' to apply.")
                cp.stage_writes(outcome["writes"])
            elif "patches" in outcome:
                print("Proposed patches (staged). Use ':w' to apply.")
                cp.stage_patches(outcome["patches"])
            elif "clarify" in outcome:
                print(outcome["clarify"])
            else:
                print(textwrap.dedent(str(outcome)))

    except (KeyboardInterrupt, EOFError):
        print("\nExiting chat.")
    finally:
        if hasattr(ai, "aclose"):
            try:
                await ai.aclose()
            except Exception:
                pass


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Bones CLI — a meta tool for code + papers")
    p.add_argument("--model", default=None, help="Override model (or BONES_MODEL env)")
    p.add_argument("--rate", default=0.5, type=float, help="Requests/sec budget")
    p.add_argument("--max-tokens", default=1200, type=int, dest="max_tokens", help="Max response tokens")
    args = p.parse_args(argv)

    model = _choose_model(args.model)
    try:
        asyncio.run(_repl(model, args.rate, args.max_tokens))
    except RuntimeError as e:
        # asyncio event loop weirdness on some platforms
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_repl(model, args.rate, args.max_tokens))
        else:
            raise


if __name__ == "__main__":
    main()
