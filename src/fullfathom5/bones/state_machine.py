from __future__ import annotations
import asyncio, json, pathlib, textwrap
from typing import Any, Dict, List


SYSTEM_SELECT = """You are a pragmatic assistant for software and papers.
Given a user question and a list of repository files, return strict JSON:
{"relevant_files":[{"path":"rel/path.py","reason":"..."}],
 "next_action":"read_files"|"ask_clarifying_question",
 "question":"only if next_action == 'ask_clarifying_question'"}"""

SYSTEM_SOLVE = """You are an AI pair-programmer.
Input will include 'question' and 'context_snippets' [{path, content}].
Return ONE of:
{"writes":[{"path":"rel.ext","content":"..."}],"message":"..."}
{"patches":[{"path":"rel.ext","unified_diff":"..."}],"message":"..."}
{"answer_md":"...","message":"..."}"""


class StateMachine:
    def __init__(self, ai_client: Any, root: pathlib.Path | None = None):
        self.ai = ai_client
        self.root = root or pathlib.Path.cwd()

    async def _call(self, system: str, user_obj: Dict) -> Dict:
        raw = await self.ai.complete(system, json.dumps(user_obj, indent=2))
        try:
            return json.loads(raw)
        except Exception:
            # Fallback to answer_md if model didn't return JSON
            return {"answer_md": textwrap.dedent(raw).strip(), "message": "Non-JSON output"}

    async def _select(self, question: str) -> Dict:
        files: List[str] = []
        for p in self.root.rglob("*"):
            if p.is_dir():
                continue
            parts = set(p.parts)
            if any(skip in parts for skip in (".git", ".bones", "__pycache__", "node_modules", "dist", "build")):
                continue
            try:
                rel = str(p.relative_to(self.root))
            except Exception:
                continue
            files.append(rel)
            if len(files) >= 400:
                break

        payload = {"question": question, "repo_files": files}
        out = await self._call(SYSTEM_SELECT, payload)
        # Minimal normalization
        if out.get("next_action") != "read_files":
            q = out.get("question") or "Could you narrow the target?"
            return {"clarify": q}
        paths = [x.get("path") for x in out.get("relevant_files", []) if x and x.get("path")]
        return {"paths": paths[:20]}

    async def _read_snippets(self, paths: List[str]) -> List[Dict[str, str]]:
        async def _read_one(rel: str):
            p = (self.root / rel).resolve()
            if not p.exists() or p.is_dir():
                return None
            data = await asyncio.to_thread(p.read_bytes)
            head = data[: 120_000]  # cap
            return {"path": rel, "content": head.decode("utf-8", errors="replace")}
        rs = await asyncio.gather(*[_read_one(r) for r in paths], return_exceptions=False)
        return [r for r in rs if r is not None]

    async def run_turn(self, question: str) -> Dict:
        sel = await self._select(question)
        if "clarify" in sel:
            return sel
        snippets = await self._read_snippets(sel.get("paths", []))
        payload = {"question": question, "context_snippets": snippets}
        out = await self._call(SYSTEM_SOLVE, payload)

        # Normalize to one of the expected shapes
        if "writes" in out or "patches" in out or "answer_md" in out:
            return out
        # Last resort: raw JSON as markdown
        return {"answer_md": "```\n" + json.dumps(out, indent=2) + "\n```"}