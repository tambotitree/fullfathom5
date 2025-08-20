# TODO

> Working list for Bones / Choral. Priorities: **P0 (now)**, **P1 (next)**, **P2 (later)**.

---

## ✅ Done / Baseline
- REPL split into `BonesRepl` (class) + thin `bones_cli.py`
- Editable input (prompt_toolkit → readline → input fallback)
- `:diff` preview (writes diffed vs disk; patches shown as-is)
- Version from git via `setuptools_scm`
- Minimal tests green

---

## P0 — Patch Application (robust, safe by default)
- [ ] Wire `:w` / `:write` to `_apply_patches.py`
  - [ ] Combine staged **patches** + **writes→unified-diff** for one pipeline
  - [ ] **Dry-run** → summary (applied/fuzzy/rejected) → confirm → real apply
  - [ ] Backups to `.bones/backups` (already in lib) and clear staging on success
- [ ] Unit tests for `_apply_patches.py`:
  - [ ] exact apply, already-applied, fuzzy-apply, reject, whitespace-ignore
  - [ ] multi-hunk file, multi-file diff, newline normalization
- [ ] CLI smoke test: stage → `:diff` → `:w` → verify on-disk result

**Acceptance:** with staged changes, `:diff` previews correctly; `:w` dry-runs, prompts, then applies with backups; tests pass.

---

## P0 — DAG State Machine (no spaghetti)
- [ ] `graph_manager.py` with a **Directed Acyclic Graph** (DAG) per flow
  - API:
    - `create_graph(name, nodes, edges, start)`
    - `add_node(graph, id, fn, meta={})`
    - `add_edge(graph, src, dst, condition=None)`
    - `get_node_next(graph, node_id, context) -> next_id`
    - `retire_graph(name)` / `end_graph(name)`
    - `export_graph(name) -> dict` / `import_graph(dict)`
    - `detect_cycle(graph) -> bool` (must be **False**)
  - Built‑in graphs:
    - `chat_default`: SELECT → CONTEXT → SOLVE → (WRITE|PATCH|ANSWER)
    - `audit_default`: COLLECT → SCORE → REPORT
- [ ] REPL integration:
  - [ ] `:g` command prints current graph (tree/ASCII) and current node
  - [ ] optional `:g export` to JSON

**Acceptance:** default graph runs one turn cleanly; `:g` prints it; cycle detection prevents loops.

---

## P0 — Command Loop Polish
- [ ] `:! <cmd>` run shell (stdout/stderr capture, non-zero exit shown)
- [ ] `:tokens`, `:m`, `:r` already present; add `:help` entry for `:diff`, `:write`, `:!`
- [ ] History persists in `.bones/history`; add optional vi-mode toggle later

---

## P1 — Choral (Full-screen)
- [ ] `ChoralRepl` uses prompt_toolkit `Application`:
  - [ ] panes: input ↔ preview (unified diff), status bar
  - [ ] keys: `Tab` toggle preview, `a` apply, `d` dry-run, `q` quit
  - [ ] path filter field for preview
- [ ] Shared backend with `BonesRepl` (no logic duplication)

**Acceptance:** can stage → open Choral (`--ui choral`) → preview → apply.

---

## P1 — Docs & UX
- [ ] README: quickstart, env (`BONES_MODEL`, `OPENAI_MODEL`), commands (`:diff`, `:write`, `:!`)
- [ ] Add `--one-shot "question"` (non-interactive) for future VS Code integration
- [ ] Example flows: “Update README”, “Refactor function”, “Add tests”

---

## P2 — VS Code & CI
- [ ] Minimal VS Code extension command → `bones --one-shot` (later: socket/JSON mode)
- [ ] GitHub Actions: test matrix, packaging, tag → release publish
- [ ] Config file `.bones/config.yaml` (model, rate, tokens, ignore globs)

---

## Nice-to-haves / Future
- [ ] Per‑hunk apply/skip UI
- [ ] Safer shell runner (`:!`) with allowlist
- [ ] Context indexing/embeddings cache
- [ ] Telemetry/debug logging (`--debug`) with redaction

---

## Open Questions
- How do we represent conditions on DAG edges (simple lambdas? names mapped to callables?)
- Exportable/importable graph DSL vs. JSON?
- Multi-agent orchestration as graphs vs. plugins?
