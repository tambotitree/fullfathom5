# src/fullfathom5/bones/choral_base.py
"""
ChoralRepl — BonesRepl with a full-screen preview for staged changes.

- Uses prompt_toolkit Application/TextArea for a scrollable, read-only diff view.
- Keys: 'q', 'Esc', or Ctrl-C to close the viewer.
- Falls back to base render_preview if prompt_toolkit is unavailable.
"""

from __future__ import annotations

from typing import Optional, Dict, Any

from .repl_base import BonesRepl

# Import prompt_toolkit pieces lazily/optionally
try:
    from prompt_toolkit.application import Application  # type: ignore
    from prompt_toolkit.layout import Layout  # type: ignore
    from prompt_toolkit.layout.containers import HSplit, Window  # type: ignore
    from prompt_toolkit.widgets import TextArea, Frame  # type: ignore
    from prompt_toolkit.key_binding import KeyBindings  # type: ignore
    from prompt_toolkit.styles import Style  # type: ignore
    _HAS_PT = True
except Exception:
    # Not installed; Choral will fall back to base behavior.
    _HAS_PT = False


class ChoralRepl(BonesRepl):
    def print_tips(self) -> None:
        super().print_tips()
        if _HAS_PT:
            self.write_line("      (choral) In preview: q/Esc/Ctrl-C to close, arrows/PgUp/PgDn to scroll.")

    async def render_preview(self, changes: Dict[str, Any], path_filter: Optional[str] = None) -> None:
        """
        Full-screen diff preview if prompt_toolkit is available, otherwise fallback to base.
        """
        if not _HAS_PT:
            # Defer to stdout/pager implementation
            await super().render_preview(changes, path_filter=path_filter)
            return

        # Ask the base to generate the diff text, but intercept printing.
        # Trick: temporarily monkeypatch _pager_print to capture output.
        # (We keep this local and safe, then restore.)
        captured: Dict[str, str] = {"text": ""}

        async def _capture(text: str) -> None:
            captured["text"] = text

        orig_pager = getattr(self, "_pager_print")

        try:
            setattr(self, "_pager_print", _capture)  # type: ignore[attr-defined]
            await super().render_preview(changes, path_filter=path_filter)
        finally:
            setattr(self, "_pager_print", orig_pager)  # restore

        diff_text = captured["text"].strip()
        if not diff_text:
            self.write_line("(no staged changes to preview)")
            return

        # Build a full-screen viewer
        kb = KeyBindings()

        @kb.add("q")
        @kb.add("escape")
        @kb.add("c-c")
        def _(event):
            event.app.exit()

        body = TextArea(
            text=diff_text,
            read_only=True,
            scrollbar=True,
            line_numbers=False,
            focus_on_click=True,
            wrap_lines=False,
        )

        frame = Frame(
            body=body,
            title="Choral — Staged Changes Preview (:diff)",
        )

        root = HSplit([
            frame,
            Window(height=1, char="-"),
        ])

        style = Style.from_dict({
            "frame.border": "ansiblue",
            "frame.title": "bold",
        })

        app = Application(
            layout=Layout(root),
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
            style=style,
        )

        # Run the full-screen app (synchronously); we're in async context, so run in a thread
        from asyncio import to_thread
        await to_thread(app.run)
