from __future__ import annotations
import shlex, subprocess, sys, textwrap, time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class CommandAction(Enum):
    NOOP = auto()
    QUIT = auto()


@dataclass
class SessionState:
    model: Optional[str] = None
    rate: float = 0.5
    max_tokens: int = 1200
    staged_writes: List[Dict[str, Any]] = field(default_factory=list)
    staged_patches: List[Dict[str, Any]] = field(default_factory=list)


class CommandProcessor:
    def __init__(self, ai: Any, state_machine: Any):
        self.ai = ai
        self.sm = state_machine
        self.session = SessionState()

    # External hooks to stage results from the state machine
    def stage_writes(self, writes: List[Dict[str, Any]]):
        self.session.staged_writes = list(writes)

    def stage_patches(self, patches: List[Dict[str, Any]]):
        self.session.staged_patches = list(patches)

    def _apply_staged(self):
        if not self.session.staged_writes and not self.session.staged_patches:
            print("Nothing staged.")
            return
        # For MVP, only writes are supported; patches are printed for now.
        if self.session.staged_patches:
            print("Patches present, but patch apply not implemented yet. Ask for 'writes' instead.")
        for w in self.session.staged_writes:
            path = w.get("path")
            content = w.get("content", "")
            if not path:
                print("Skipping a write with no 'path'")
                continue
            from pathlib import Path
            p = Path(path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".bones.new")
            tmp.write_text(content, encoding="utf-8")
            # auto-accept for MVP; you can add confirmation later:
            tmp.replace(p)
            print(f"✓ wrote {p}")
        self.session.staged_writes.clear()
        self.session.staged_patches.clear()

    def _help(self):
        print(textwrap.dedent("""\
            Bones commands:
              :q               Quit
              :w               Apply staged writes/patches
              :wq              Apply staged writes then quit
              :m <model>       Set model (e.g., :m gpt-4o or :m llama3.1:8b)
              :r <rate>        Set rate (requests/sec), e.g., :r 0.7
              :tokens <n>      Set max response tokens
              ::text           Send literal ':text' to the model
              :help            This help
        """))

    def handle(self, line: str) -> CommandAction:
        try:
            parts = shlex.split(line[1:].strip())  # drop leading ':'
        except Exception:
            print("Could not parse command. Try ':help'.")
            return CommandAction.NOOP
        if not parts:
            return CommandAction.NOOP

        cmd, *rest = parts

        if cmd in {"q", "quit", "exit"}:
            return CommandAction.QUIT

        if cmd in {"w", "write"}:
            self._apply_staged()
            return CommandAction.NOOP

        if cmd == "wq":
            self._apply_staged()
            return CommandAction.QUIT

        if cmd == "m":
            if not rest:
                print(f"model = {getattr(self.ai, 'model', None)}")
            else:
                newm = rest[0]
                old = getattr(self.ai, "model", None)
                setattr(self.ai, "model", newm)
                self.session.model = newm
                print(f"model → {newm} (was {old})")
            return CommandAction.NOOP

        if cmd == "r":
            if not rest:
                print(f"rate = {self.session.rate}")
            else:
                try:
                    self.session.rate = float(rest[0])
                    print(f"rate → {self.session.rate}")
                except Exception:
                    print("Usage: :r <float>")
            return CommandAction.NOOP

        if cmd in {"tokens", "max", "max_tokens"}:
            if not rest:
                print(f"max_tokens = {self.session.max_tokens}")
            else:
                try:
                    self.session.max_tokens = int(rest[0])
                    setattr(self.ai, "max_tokens", self.session.max_tokens)
                    print(f"max_tokens → {self.session.max_tokens}")
                except Exception:
                    print("Usage: :tokens <int>")
            return CommandAction.NOOP

        if cmd in {"help", "?"}:
            self._help()
            return CommandAction.NOOP

        print("Unknown command. Try ':help'.")
        return CommandAction.NOOP