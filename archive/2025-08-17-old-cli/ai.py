# src/fullfathom5/ai.py
import os
import asyncio
import contextlib
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Order by capability; first is used when nothing is specified
PREFERRED_MODELS: List[str] = [
    "gpt-5",         # highest, if available to your key
    "gpt-4.1",
    "gpt-4o",
    "gpt-4o-mini",
]

def _select_model(requested: Optional[str]) -> str:
    """
    Choose a model by priority:
      1) explicit request (CLI flag)
      2) BONES_MODEL env var
      3) first of PREFERRED_MODELS
    """
    if requested and requested.strip():
        return requested.strip()
    env_model = os.getenv("BONES_MODEL", "").strip()
    if env_model:
        return env_model
    return PREFERRED_MODELS[0]

def _dag_hint_prefix() -> str:
    """
    Optional small, non-invasive system hint to help with DAG/branch talk.
    Enable with BONES_DAG_HINT=1 (default off).
    """
    if os.getenv("BONES_DAG_HINT", "").strip() not in {"1", "true", "True"}:
        return ""
    return (
        "You are Bones, a meta-assistant reasoning over a project DAG of chats and branches. "
        "When the user refers to branches (e.g., 'AI Bridge') or a living DAG of projects, "
        "infer they want context-routing, not physics results. Ask 1 clarifying question only "
        "if routing is ambiguous; otherwise proceed."
    )

class _FallbackAIClient:
    """
    Minimal async AI client using OpenAI if available, otherwise raises helpful error.
    Surface matches eqnlint.lib._ai.AIClient for .complete()/.aclose().
    """
    def __init__(self, model: str = "gpt-4o-mini", rate: float = 0.5, max_tokens: int = 4096):
        load_dotenv()
        self.model = model
        self.max_tokens = max_tokens
        self._client = None
        self._system_hint = _dag_hint_prefix()

    async def _ensure(self):
        if self._client is not None:
            return
        # Lazy import; support both import styles
        try:
            try:
                # Newer SDK style
                from openai import AsyncOpenAI  # type: ignore
                AsyncClient = AsyncOpenAI
            except Exception:
                # Fallback if user imported differently
                import openai  # type: ignore
                AsyncClient = getattr(openai, "AsyncOpenAI")
        except Exception as e:
            raise RuntimeError(
                "OpenAI SDK not installed. Install `openai` or ensure eqnlint is importable."
            ) from e

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY missing (add it to your .env).")

        base_url = os.getenv("OPENAI_BASE_URL") or None
        org = os.getenv("OPENAI_ORG") or None

        # Construct client with optional base_url/org
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if org:
            kwargs["organization"] = org
        self._client = AsyncClient(**kwargs)

    async def complete(self, system: str, user: str, fewshot: Optional[List[Dict[str, str]]] = None) -> str:
        await self._ensure()
        sys_content = system
        if self._system_hint:
            sys_content = f"{self._system_hint}\n\n{system}"

        msgs: List[Dict[str, str]] = [{"role": "system", "content": sys_content}]
        if fewshot:
            msgs += fewshot
        msgs.append({"role": "user", "content": user})

        # Support either chat.completions or responses depending on SDK; prefer chat.completions
        try:
            resp = await self._client.chat.completions.create(  # type: ignore[attr-defined]
                model=self.model,
                messages=msgs,
                temperature=0,
                max_tokens=self.max_tokens,
            )
            content = resp.choices[0].message.content or ""
        except AttributeError:
            # If using the newer Responses API only
            resp = await self._client.responses.create(  # type: ignore[attr-defined]
                model=self.model,
                input=msgs,
                temperature=0,
                max_output_tokens=self.max_tokens,
            )
            # Normalize content from responses API
            content_chunks = []
            for out in getattr(resp, "output", []) or []:
                if getattr(out, "type", None) == "message":
                    for part in getattr(out, "content", []) or []:
                        if getattr(part, "type", None) == "output_text":
                            content_chunks.append(getattr(part, "text", "") or "")
            content = "".join(content_chunks)

        return (content or "").strip()

    async def aclose(self):
        if not self._client:
            return
        close = getattr(self._client, "aclose", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()
        # Yield control back to loop
        await asyncio.sleep(0)

def get_ai_client(model: Optional[str] = None, rate: float = 0.5, max_tokens: int = 4096):
    """
    Try to return eqnlint's AIClient. If that fails, return the fallback client.
    Model can be overridden via:
      - CLI flag: --model
      - env var: BONES_MODEL
    """
    chosen = _select_model(model)
    try:
        from eqnlint.lib._ai import AIClient as EqnAIClient  # type: ignore
        # Preserve your async surface and parameters
        return EqnAIClient(model=chosen, rate=rate, max_tokens=max_tokens)
    except Exception:
        # Use our minimal async fallback
        return _FallbackAIClient(model=chosen, rate=rate, max_tokens=max_tokens)
