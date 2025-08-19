# tests/test_cli.py
import os
import types
import builtins
import importlib
import pytest

# Import the CLI module
from fullfathom5.bones import bones_cli as cli


def test_version_flag_exits_cleanly(monkeypatch):
    """
    argparse's 'version' action raises SystemExit after printing.
    We only assert that the call exits (exit code 0 or 2 are acceptable depending on platform handling).
    """
    # Capture SystemExit from main(["--version"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    # Some environments use code 0 for version; click/argparse variations sometimes use 0 or 2.
    assert excinfo.value.code in (0, None)


def test_choose_model_env_override(monkeypatch):
    # No CLI arg; env should win
    monkeypatch.delenv("BONES_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert cli._choose_model(None) == "gpt-4o-mini"

    monkeypatch.setenv("BONES_MODEL", "ollama:llama3.1:8b")
    assert cli._choose_model(None) == "ollama:llama3.1:8b"

    # OPENAI_MODEL also supported; BONES_MODEL should take precedence if both set
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert cli._choose_model(None) == "ollama:llama3.1:8b"

    # If CLI arg provided, it overrides env
    assert cli._choose_model("gpt-4o-mini") == "gpt-4o-mini"


def test_repl_class_imports():
    """
    Importing the REPL class should not raise—even if we don't instantiate it.
    This catches stray syntax issues and misplaced future imports, etc.
    """
    from fullfathom5.bones.repl_base import BonesRepl  # or bones_repl if that's your filename
    assert hasattr(BonesRepl, "__init__")
