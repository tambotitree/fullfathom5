# repl_choral.py
from .repl_base import BonesRepl

class ChoralRepl(BonesRepl):
    async def render_preview(self, changes: dict) -> None:
        # For now: just call the base implementation.
        # Later: replace with prompt_toolkit full-screen diff viewer.
        await super().render_preview(changes)
