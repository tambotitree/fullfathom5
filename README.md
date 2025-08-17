# fullfathom5 (Bones)

**Bones** is a file-aware, context-driven CLI for LLM workflows.
It asks the model which files matter, pulls context, and proposes patches/snippets.

## Install (dev)
```bash
pip install -e .
bones version

# FullFathom5 VS Code Extension

Provides a minimal bridge to the `bones` CLI for context-aware code & paper workflows.

## Commands

- **FullFathom5: Hello Bones** — sanity check.
- *(example)* **FullFathom5: Run Bones Ask** — shell out to `bones` (interactive).

> Tip: adapt your `bones` CLI to support a non-interactive `--one-shot "<question>"` mode, then wire it here.

## Quickstart

# in the extension folder (repo root)
npm init -y                      # if you don't already have a package.json (but we provided one)
npm install
npm run compile
# Open the folder in VS Code, press F5 to launch Extension Development Host
