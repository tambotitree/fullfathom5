# src/fullfathom5/bones/bones_engine.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union, Iterable
from .commands import CommandAction  # if not already imported

# ---- Events (inputs to the engine)
@dataclass
class EvUserText:
    text: str

@dataclass
class EvCommand:
    name: str
    args: str

# ---- Effects (outputs from the engine; REPL performs them)
@dataclass
class EffAskModel:
    prompt: str
    params: Dict[str, Any]

@dataclass
class EffPreview:
    changes: Dict[str, Any]
    path_filter: Optional[str] = None

@dataclass
class EffApplyPatches:
    patches: List[Dict[str, str]]  # {"path","unified_diff"}
    dry_run: bool
    options: Dict[str, Any]

@dataclass
class EffRunShell:
    cmd: str

@dataclass
class EffRenderText:
    text: str

Effect = Union[EffAskModel, EffPreview, EffApplyPatches, EffRunShell, EffRenderText]

# ---- Engine result
@dataclass
class EngineStep:
    effects: List[Effect]
    # optional status or next-node hints later
    # meta: Dict[str, Any] = None
    meta: Optional[Dict[str, Any]] = None

class BonesEngine:
    """
    Shim: accepts Events, emits Effects. Initially proxies to StateMachine/CommandProcessor.
    """
    def __init__(self, state_machine, command_processor):
        self.sm = state_machine
        self.cp = command_processor

    async def handle(self, ev: Union[EvUserText, EvCommand]) -> EngineStep:
        if isinstance(ev, EvCommand):
            action = self.cp.handle(f":{ev.name} {ev.args}".strip())
            meta = {"cmd_action": action}
            return EngineStep(effects=[], meta=meta)
        elif isinstance(ev, EvUserText):
            outcome = await self.sm.run_turn(ev.text)
            effs: List[Effect] = []
            if "answer_md" in outcome:
                effs.append(EffRenderText(outcome["answer_md"]))
            elif "writes" in outcome:
                effs.append(EffPreview({"writes": outcome["writes"], "patches": []}))
            elif "patches" in outcome:
                effs.append(EffPreview({"writes": [], "patches": outcome["patches"]}))
            elif "clarify" in outcome:
                effs.append(EffRenderText(outcome["clarify"]))
            else:
                effs.append(EffRenderText(str(outcome)))
            return EngineStep(effects=effs)
        else:
            return EngineStep(effects=[EffRenderText(f"Unknown event: {ev}")])