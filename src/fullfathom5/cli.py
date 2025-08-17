#!/usr/bin/env python3
# bones — One CLI for papers (eqnlint) + codebases (context-aware chat)

import os, sys, json, textwrap, asyncio, pathlib, shutil, subprocess
from typing import Any, List, Dict
import click
from dotenv import load_dotenv

project_name = "fullfathom5"
# Try to read pyproject.toml for [project].name at runtime; else default.

select_rules = {
  "project_name": project_name,
  "do_not_create_new_projects": True,
  "preferred_targets": ["VS Code extension", "commands", "JSON outputs", "writes"]
}

# ---------- Paths / cache ----------
ROOT = pathlib.Path.cwd()
CACHE_DIR = ROOT / ".bones"
CACHE_DIR.mkdir(exist_ok=True)

# ---------- Env ----------
def load_env():
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        click.secho("WARN: OPENAI_API_KEY not found in environment (.env?)", fg="yellow")

# ---------- Helpers ----------
def safe_relpath(p: str) -> str:
    full = (ROOT / p).resolve()
    if ROOT not in full.parents and full != ROOT:
        raise ValueError(f"Refusing to access path outside project: {p}")
    return str(full.relative_to(ROOT))

def read_text_max(path: pathlib.Path, max_bytes=200_000) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        head = data[: max_bytes]
        return head.decode("utf-8", errors="replace") + "\n\n[...TRUNCATED...]"
    return data.decode("utf-8", errors="replace")

def try_json(s: str):
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s.replace("json", "", 1).strip()
    try:
        return json.loads(s)
    except Exception:
        return None

async def human_confirm_async(prompt: str, default_yes=True) -> bool:
    yn = "Y/n" if default_yes else "y/N"
    resp = (await asyncio.to_thread(input, f"{prompt} [{yn}] ")).strip().lower()
    if not resp:
        return default_yes
    return resp in ("y", "yes")

# ---------- Prompts ----------
SYSTEM_SELECT = """You are a pragmatic assistant for software and scientific papers.
Given a user question and the list of repository files, decide which files are most relevant.
Return strict JSON:

{
  "relevant_files": [{"path":"rel/path.py","reason":"..."}],
  "next_action": "read_files" | "ask_clarifying_question",
  "question": "only if next_action == 'ask_clarifying_question'"
}

Only include files that truly help the request. Prefer specific files over directories.
"""

SYSTEM_SOLVE = """You are an AI pair-programmer and paper editor.
You will receive the user question and a set of file snippets (with paths).
Respond as strict JSON using ONE of these shapes:

1) Writes full files:
{
  "writes": [
    {"path":"rel/file.ext","content":"full new file content"}
  ],
  "message": "summary of changes"
}

2) Patches (unified diff):
{
  "patches": [
    {"path":"rel/file.ext","unified_diff":"--- a/...\\n+++ b/...\\n@@"}
  ],
  "message": "summary of changes"
}

3) Answer only:
{
  "answer_md": "# Explanation ...",
  "message": "short summary"
}

If uncertain, ask for clarification via:
{"answer_md":"I need clarification: ...", "message":"..."}
"""

# ---------- Model wiring (lazy import) ----------
def make_ai_client(model: str, rate: float, max_tokens: int):
    """
    Lazy-create the eqnlint AI client so 'bones --help' or 'bones version'
    don't require eqnlint to be installed/importable.
    """
    try:
        from eqnlint.lib._ai import AIClient  # type: ignore
    except Exception as e:
        click.secho("[ERROR] Could not import eqnlint.lib._ai.AIClient — add project to PYTHONPATH or install eqnlint.", fg="red")
        raise
    return AIClient(model=model, rate=rate, max_tokens=max_tokens)

async def call_model(ai: Any, system: str, user: str, fewshot=None) -> str:
    return await ai.complete(system, user, fewshot=fewshot or [])

# ---------- File selection & solving ----------
async def choose_relevant(ai: Any, user_query: str) -> Dict:
    file_list: List[str] = []
    for p in ROOT.rglob("*"):
        if p.is_dir():
            continue
        parts = set(p.parts)
        if any(skip in parts for skip in (".git", ".bones", ".tibo", "__pycache__", "dist", "build", ".venv", "node_modules")):
            continue
        try:
            rel = str(p.relative_to(ROOT))
        except Exception:
            continue
        file_list.append(rel)
        if len(file_list) >= 600:
            break

    user = json.dumps({"question": user_query, "repo_files": file_list}, indent=2)
    raw = await call_model(ai, SYSTEM_SELECT, user)
    return try_json(raw) or {
        "relevant_files": [],
        "next_action": "ask_clarifying_question",
        "question": "Could you narrow the target?",
    }

async def solve_with_context(ai: Any, user_query: str, files: List[str]) -> Dict:
    async def _read_one(rel: str):
        try:
            rel = safe_relpath(rel)
            p = ROOT / rel
            if not p.exists() or p.is_dir():
                return None
            content = await asyncio.to_thread(read_text_max, p)
            return {"path": rel, "content": content}
        except Exception as e:
            return {"path": rel, "content": f"[ERROR reading file: {e}]"}
    results = await asyncio.gather(*[_read_one(r) for r in files], return_exceptions=False)
    snippets = [r for r in results if r is not None]

    user = json.dumps({"question": user_query, "context_snippets": snippets}, indent=2)
    raw = await call_model(ai, SYSTEM_SOLVE, user)
    return try_json(raw) or {"answer_md": raw, "message": "Non-JSON model output (preserved as answer_md)."}

async def apply_writes(changes: List[Dict]):
    for w in changes:
        rel = safe_relpath(w["path"])
        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".bones.new")
        tmp.write_text(w["content"], encoding="utf-8")
        if await human_confirm_async(f"Write/overwrite {rel} ?", default_yes=True):
            await asyncio.to_thread(shutil.move, str(tmp), str(dest))
            click.secho(f"✓ wrote {rel}", fg="green")
        else:
            tmp.unlink(missing_ok=True)
            click.secho(f"skipped {rel}", fg="yellow")

async def apply_patches(patches: List[Dict]):
    for p in patches:
        rel = safe_relpath(p["path"])
        dest = ROOT / rel
        if not dest.exists():
            click.secho(f"File for patch not found: {rel}", fg="red")
            continue
        click.secho(f"--- Proposed patch for {rel} ---", fg="cyan")
        print(p["unified_diff"])
        if await human_confirm_async(f"Apply patch to {rel}? (Will try textual diff recompose)", default_yes=False):
            click.secho("Patch application not yet implemented; ask model to return 'writes' with full file content.", fg="yellow")

# ---------- CLI ----------
@click.group(context_settings=dict(help_option_names=["-h", "--help"]))
def cli():
    """Bones — minimal context-aware CLI for code + papers."""
    pass

@cli.command()
def version():
    """Show Bones and package version."""
    try:
        from importlib.metadata import version as _v
        pkg_ver = _v("fullfathom5")
    except Exception:
        pkg_ver = "0.0.0"
    click.secho(f"Bones / fullfathom5 {pkg_ver}", fg="cyan")

@cli.command()
@click.option("-f", "--file", "file_", required=True, help="LaTeX file to audit with eqnlint")
@click.option("-o", "--output", help="Write human log to file")
@click.option("--json", "json_out", help="Write JSON to file")
@click.option("--model", default="gpt-4o-mini", show_default=True)
@click.option("--rate", default=0.5, show_default=True, type=float)
@click.option("--max-tokens", default=1200, show_default=True, type=int)
def audit(file_, output, json_out, model, rate, max_tokens):
    """Run eqnlint across audits (paper path)."""
    load_env()
    args = ["eqnlint", "-f", file_]
    if output:
        args += ["-o", output]
    if json_out:
        args += ["--json", json_out]
    args += ["--model", model, "--rate", str(rate), "--max-tokens", str(max_tokens)]
    click.secho(f"Running: {' '.join(args)}", fg="cyan")
    rc = subprocess.call(args)
    sys.exit(rc)

@cli.command()
@click.option("--model", default="gpt-4o-mini", show_default=True)
@click.option("--rate", default=0.5, show_default=True, type=float)
@click.option("--max-tokens", default=1200, show_default=True, type=int)
def chat(model, rate, max_tokens):
    """Interactive chat for codebases that auto-pulls relevant files and proposes edits."""
    load_env()

    async def _loop():
        ai = make_ai_client(model=model, rate=rate, max_tokens=max_tokens)
        try:
            click.secho("bones chat — type your request (Ctrl-C to exit)", fg="cyan", bold=True)
            while True:
                query = (await asyncio.to_thread(input, "\nYou> ")).strip()
                if not query:
                    continue

                sel = await choose_relevant(ai, query)
                revs = [item["path"] for item in sel.get("relevant_files", [])]
                if sel.get("next_action") == "ask_clarifying_question":
                    click.secho(sel.get("question", "Need clarification."), fg="yellow")
                    continue

                if not revs:
                    click.secho("No relevant files suggested; continuing without context.", fg="yellow")

                result = await solve_with_context(ai, query, revs)

                if "writes" in result:
                    click.secho(result.get("message", "Proposed writes:"), fg="green")
                    await apply_writes(result["writes"])
                elif "patches" in result:
                    click.secho(result.get("message", "Proposed patches:"), fg="green")
                    await apply_patches(result["patches"])
                elif "answer_md" in result:
                    click.secho("Answer:", fg="green")
                    print(textwrap.dedent(result["answer_md"]).strip())
                else:
                    click.secho("Model returned unrecognized JSON; printing raw:", fg="yellow")
                    print(json.dumps(result, indent=2))
        except (KeyboardInterrupt, EOFError):
            click.secho("\nExiting chat.", fg="cyan")
        finally:
            if hasattr(ai, "aclose"):
                try:
                    await ai.aclose()
                except Exception:
                    pass

    asyncio.run(_loop())

def main():
    cli()

if __name__ == "__main__":
    main()
