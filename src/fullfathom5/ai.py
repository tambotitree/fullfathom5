# src/fullfathom5/ai.py
import os
import asyncio
import contextlib
from dotenv import load_dotenv

class _FallbackAIClient:
    """
    Minimal async AI client using OpenAI if available, otherwise raises helpful error.
    Has same surface as eqnlint.lib._ai.AIClient for .complete()/.aclose().
    """
    def __init__(self, model="gpt-4o-mini", rate=0.5, max_tokens=1200):
        load_dotenv()
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    async def _ensure(self):
        if self._client is None:
            try:
                import openai
            except Exception as e:
                raise RuntimeError(
                    "OpenAI SDK not installed. Install `openai` or ensure eqnlint is importable."
                ) from e
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY missing (add it to your .env).")
            self._client = openai.AsyncOpenAI(api_key=api_key)

    async def complete(self, system, user, fewshot=None):
        await self._ensure()
        msgs = [{"role": "system", "content": system}]
        if fewshot:
            msgs += fewshot
        msgs.append({"role": "user", "content": user})
        resp = await self._client.chat.completions.create(
            model=self.model, messages=msgs, temperature=0, max_tokens=self.max_tokens
        )
        content = resp.choices[0].message.content or ""
        return content.strip()

    async def aclose(self):
        if not self._client:
            return
        close = getattr(self._client, "aclose", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()
        await asyncio.sleep(0)

def get_ai_client(model="gpt-4o-mini", rate=0.5, max_tokens=1200):
    """
    Try to return eqnlint's AIClient. If that fails, return the fallback client.
    """
    try:
        from eqnlint.lib._ai import AIClient as EqnAIClient  # type: ignore
        return EqnAIClient(model=model, rate=rate, max_tokens=max_tokens)
    except Exception:
        # Use our minimal fallback
        return _FallbackAIClient(model=model, rate=rate, max_tokens=max_tokens)
