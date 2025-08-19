#!/usr/bin/env python3
# src/fullfathom5/bones/bones_repl.py
"""
BonesRepl — thin adapter around the state machine + command processor.

Features:
- Editable input line (prompt_toolkit if available; else readline; else input()).
- Small-talk fastpath for trivial greetings.
- Staging of writes/patches via CommandProcessor.
- Diff/preview seam (stdout with pager). Choral can override render_preview for full-screen.
"""

from __future__ import annotations

import os
import re
import atexit
import asyncio
import pathlib
import shutil
import subprocess
import textwrap
import difflib
from typing import Optional, Callable, List, Dict, Any
from ._apply_patches import apply_patches, ApplyOptions

# Optional UI libs (best UX)
try:
    from prompt_toolkit import PromptSession  # type: ignore
    from prompt_toolkit.history import FileHistory  # type: ignore
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory  # type: ignore
except Exception:  # pragma: no cover
    PromptSession = None  # type: ignore
    FileHistory = None  # type: ignore
    AutoSuggestFromHistory = None  # type: ignore

# readline fallback (POSIX/macOS)
try:
    import readline  # type: ignore
except Exception:  # pragma: no cover
    readline = None  # type: ignore

# Core wiring
from eqnlint.lib._ai import AIClient  # single source of truth
from .state_machine import StateMachine
from .commands import CommandProcessor, CommandAction


class BonesRepl:
    """
    Minimal, subclassable REPL.
    Override render_preview (and optionally read_line / write_line) in Choral for full-screen UI.
    """

    def __init__(self, model: str, rate: float, max_tokens: int):
        # config
        self.model = model
        self.rate = rate
        self.max_tokens = max_tokens

        # engine pieces
        self.ai = AIClient(model=model, rate=rate, max_tokens=max_tokens)
        self.sm = StateMachine(self.ai)
        self.cp = CommandProcessor(self.ai, self.sm)

        # input setup
        self._get_line: Callable[[str], str] = self._setup_line_input()
        # local staging for preview
        self._staged_writes: List[Dict[str, Any]] = []
        self._staged_patches: List[Dict[str, Any]] = []

    # ---------- I/O setup ----------
    def _setup_line_input(self) -> Callable[[str], str]:
        """
        Returns get_line(prompt: str) -> str with history + editing.
        Prefers prompt_toolkit; falls back to readline; then to plain input().
        """
        hist_dir = pathlib.Path(".bones")
        hist_dir.mkdir(exist_ok=True)
        hist_file = hist_dir / "history"

        if PromptSession is not None:
            session = PromptSession(
                history=FileHistory(str(hist_file)) if FileHistory else None,
                auto_suggest=AutoSuggestFromHistory() if AutoSuggestFromHistory else None,
            )
            return lambda prompt: session.prompt(prompt)

        if readline is not None:
            try:
                readline.parse_and_bind("set editing-mode emacs")
                readline.parse_and_bind("tab: complete")
            except Exception:
                pass
            try:
                if hist_file.exists():
                    readline.read_history_file(str(hist_file))
            except Exception:
                pass

            def _save_history():
                try:
                    readline.write_history_file(str(hist_file))
                except Exception:
                    pass

            atexit.register(_save_history)
            return input

        return input

    # ---------- Small helpers ----------
    # inside class BonesRepl (near other small helpers)
    def _writes_to_unified_diffs(self, writes: list[dict]) -> list[dict]:
        """Turn [{"path","content"}] into [{"path","unified_diff"}] against current disk."""
        diffs: list[dict] = []
        for w in writes or []:
            rel = w.get("path", "")
            new_text = w.get("content", "")
            try:
                old_text = pathlib.Path(rel).read_text(encoding="utf-8", errors="replace")
            except Exception:
                old_text = ""
            ud = "".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=3,
            ))
            if ud:
                diffs.append({"path": rel, "unified_diff": ud})
        return diffs

    @staticmethod
    def _small_talk_fastpath(s: str) -> Optional[str]:
        """Return a canned response (or empty string to no-op) if input is trivial."""
        t = s.strip().lower()
        if not t:
            return ""
        if t in {"hi", "hello", "yo", "hey", "thanks", "thank you", "sup", "hola"}:
            return "Hi! What do you want to do in this repo?"
        if re.fullmatch(r"\s*(please\s+)?(say\s+)?(hi|hello)\s*[.!]?\s*", t):
            return "Hello! Ready when you are."
        if re.fullmatch(r"\s*(please\s+)?greet\s*(me)?\s*[.!]?\s*", t):
            return "Hello! How can I help in this codebase?"
        return None

    def print_tips(self) -> None:
        print("bones chat — type your request (Ctrl-C to exit)")
        print("tips: ':q' quit, ':w' (aka :write) apply writes, ':m <model>', ':r <rate>', ':tokens <n>', '::text' to send leading colon.")
        print("      ':diff' previews staged changes. Use a path to filter: ':diff src/foo.py'.")

    def write_line(self, text: str) -> None:
        print(text)

    async def read_line(self, prompt: str) -> str:
        # avoid blocking the event loop on stdin
        return (await asyncio.to_thread(self._get_line, prompt)).rstrip("\n")

    async def confirm(self, prompt: str, default_yes: bool = True) -> bool:
        yn = "Y/n" if default_yes else "y/N"
        ans = (await self.read_line(f"{prompt} [{yn}] ")).strip().lower()
        if not ans:
            return default_yes
        return ans in {"y", "yes"}

    # ---------- Preview/diff ----------
    async def render_preview(self, changes: Dict[str, Any], path_filter: Optional[str] = None) -> None:
        """
        Preview staged changes. `changes` is typically:
          {"writes":[{"path":..., "content":...}], "patches":[{"path":..., "unified_diff":...}]}
        If `path_filter` is provided, only show diffs for matching paths (substring match).
        """
        pieces: List[str] = []

        # 1) Patches: show as-is
        for p in changes.get("patches", []) or []:
            rel = p.get("path", "")
            if path_filter and path_filter not in rel:
                continue
            udiff = p.get("unified_diff", "")
            if udiff:
                pieces.append(udiff if udiff.endswith("\n") else udiff + "\n")

        # 2) Writes: compute unified diff vs disk
        for w in changes.get("writes", []) or []:
            rel = w.get("path", "")
            if path_filter and path_filter not in rel:
                continue
            new_content = w.get("content", "")
            new_lines = new_content.splitlines(keepends=True)
            old_lines: List[str]
            try:
                old_lines = pathlib.Path(rel).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            except Exception:
                old_lines = []

            ud = difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=3,
            )
            block = "".join(ud)
            if block:
                pieces.append(block if block.endswith("\n") else block + "\n")

        out = "".join(pieces).rstrip()
        if not out:
            self.write_line("(no staged changes to preview)")
            return

        await self._pager_print(out)

    async def _pager_print(self, text: str) -> None:
        """
        Print via $PAGER (less/more) if available, else to stdout.
        """
        pager = os.environ.get("PAGER")
        if not pager:
            if shutil.which("less"):
                pager = "less"
            elif shutil.which("more"):
                pager = "more"
        if pager:
            try:
                # Run pager; ensure it doesn't hang on stdin closing
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [pager],
                    input=text.encode("utf-8", errors="ignore"),
                    check=False,
                )
                return
            except Exception:
                pass
        # Fallback: plain print
        print(text)

    # ---------- Command entry (hook for subclasses if needed) ----------
    def handle_command(self, raw: str) -> Optional[CommandAction]:
        """
        Dispatch a colon command. Returns a CommandAction or None if not a control flow action.
        """
        return self.cp.handle(raw)

    def _summ_counts(self, file_reports):
        ok = fuzz = rej = 0
        for fr in file_reports:
            # A file is “ok” if some hunk applied exactly
            # exact_applied = any(hr.applied and hr.exact for hr in fr.hunk_reports)
            exact_applied = any(hr.applied and hr.exact_match for hr in fr.hunk_reports)
            # “fuzzy” if some hunk applied but not exact
            # fuzzy_applied = any(hr.applied and not hr.exact for hr in fr.hunk_reports)
            fuzzy_applied = any(hr.applied and not hr.exact_match for hr in fr.hunk_reports)
            # “rejected” if no hunks applied and none were already-applied
            none_applied = not any(hr.applied for hr in fr.hunk_reports)
            already_all  = all(hr.already_applied for hr in fr.hunk_reports) if fr.hunk_reports else False

            if exact_applied:
                ok += 1
            elif fuzzy_applied:
                fuzz += 1
            elif none_applied and not already_all:
                rej += 1
            # if already_all: it was a no-op; don’t count as reject
        return ok, fuzz, rej

    # ---------- Main loop ----------
    async def run(self) -> None:
        self.print_tips()
    
        try:
            while True:
                line = await self.read_line("\nYou> ")
    
                # 1) Colon-commands
                if line.startswith(":"):
                    # "::foo" -> send ":foo" literally to the model
                    if line.startswith("::"):
                        line = line[1:]
                    else:
                        # :diff [path_filter]
                        if line.startswith(":diff"):
                            _, _, filt = line.partition(" ")
                            filt = (filt or "").strip() or None
                            staged = {"writes": self._staged_writes, "patches": self._staged_patches}
                            await self.render_preview(staged, path_filter=filt)
                            continue
    
                        # :write / :w  → dry-run apply patches, confirm, then real apply
                        if line.strip() in (":write", ":w"):
    
                            if not self._staged_patches and not self._staged_writes:
                                self.write_line("Nothing staged.")
                                continue
    
                            # Dry run
                            opts = ApplyOptions(
                                dry_run=True,
                                ratio_cutoff=0.70,
                                ignore_space=False,
                            )
                            combined = list(self._staged_patches)
                            combined += self._writes_to_unified_diffs(self._staged_writes)

                            # On confirm
                            opts = ApplyOptions(dry_run=True, ratio_cutoff=0.70, ignore_space=False)
                            reports = apply_patches(combined, options=opts)

                            # Summarize dry run
                            # ok = sum(1 for r in reports if r.get("status") == "applied")
                            # fuzz = sum(1 for r in reports if r.get("status") == "fuzzy")
                            # rej = sum(1 for r in reports if r.get("status") == "rejected")
                            ok, fuzz, rej = self._summ_counts(reports)
                            self.write_line(f"Dry-run: applied={ok}, fuzzy={fuzz}, rejected={rej}")

                            # Confirm
                            confirm = (await self.read_line("Apply patches for real? [y/N] ")).strip().lower()
                            if confirm.startswith("y"):
                                opts = ApplyOptions(dry_run=False, ratio_cutoff=0.70, ignore_space=False)
                                reports = apply_patches(combined, options=opts)
                                ok, fuzz, rej = self._summ_counts(reports)
                                self.write_line(f"Apply:   applied={ok}, fuzzy={fuzz}, rejected={rej}")
                                # Clear staging after real apply
                                self._staged_patches.clear()
                                self._staged_writes.clear()
                            else:
                                self.write_line("Aborted write.")
                            continue
    
                        # Everything else goes to CommandProcessor
                        action = self.handle_command(line)
                        if action is CommandAction.QUIT:
                            self.write_line("Exiting chat.")
                            break
    
                    # important: stay inside colon-commands branch
                    continue
    
                # 2) Small-talk fast path
                fast = self._small_talk_fastpath(line)
                if fast is not None:
                    if fast:
                        self.write_line(fast)
                    continue
    
                # 3) Normal state-machine turn
                outcome = await self.sm.run_turn(line)
                if "answer_md" in outcome:
                    self.write_line(textwrap.dedent(outcome["answer_md"]).strip())
                elif "writes" in outcome:
                    self.write_line("Proposed writes (staged). Use ':diff' to preview or ':w' to apply.")
                    self._staged_writes = list(outcome["writes"])   # keep copy for :diff
                    self.cp.stage_writes(outcome["writes"])
                elif "patches" in outcome:
                    self.write_line("Proposed patches (staged). Use ':diff' to preview or ':w' to apply.")
                    self._staged_patches = list(outcome["patches"]) # keep copy for :diff
                    self.cp.stage_patches(outcome["patches"])
                elif "clarify" in outcome:
                    self.write_line(outcome["clarify"])
                else:
                    self.write_line(textwrap.dedent(str(outcome)))
    
        except (KeyboardInterrupt, EOFError):
            self.write_line("\nExiting chat.")
        finally:
            if hasattr(self.ai, "aclose"):
                try:
                    await self.ai.aclose()
                except Exception:
                    pass
