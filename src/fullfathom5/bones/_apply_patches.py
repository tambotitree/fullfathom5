# src/fullfathom5/bones/_apply_patches.py
"""
Pure-Python unified-diff patch applier for Bones.

Goals
-----
- Accept either:
  1) A list of {"path": "...", "unified_diff": "..."} dicts, or
  2) A single unified diff string that may contain multiple files.
- Dry-run first; report exactly what would change.
- Apply with fuzz using difflib when exact anchors don't match.
- Be idempotent: if a hunk is already applied, skip it cleanly.
- Keep backups when writing (to .bones/backups/<path>.bak.<ts>).

Non-goals (MVP)
---------------
- No binary patches, copies/renames, or permission bits.
- No git index metadata interpretation.
"""

from __future__ import annotations

import os
import re
import io
import sys
import time
import pathlib
import difflib
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Iterable

# ---------- Data types ----------

@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str]  # raw hunk lines including prefixes ' ', '-', '+'

@dataclass
class FilePatch:
    path: str
    hunks: List[Hunk] = field(default_factory=list)

@dataclass
class HunkReport:
    index: int
    applied: bool
    already_applied: bool
    exact_match: bool
    ratio: float
    notes: str = ""

@dataclass
class FileReport:
    path: str
    applied: bool
    changed_lines: int
    hunk_reports: List[HunkReport]
    notes: str = ""
    preview_diff: Optional[str] = None

@dataclass
class ApplyOptions:
    dry_run: bool = True
    repo_root: str = "."
    ratio_cutoff: float = 0.66
    ignore_space: bool = False
    normalize_newlines: bool = True
    backups_dir: str = ".bones/backups"
    generate_preview: bool = True


# ---------- Public entry points ----------

def apply_patches(
    patches: Iterable[Dict[str, str]] | str,
    *,
    options: ApplyOptions | None = None,
    path_filter: Optional[str] = None,
) -> List[FileReport]:
    """
    Apply patches to the working tree.

    Parameters
    ----------
    patches : iterable of {"path","unified_diff"} OR unified diff string
    options : ApplyOptions
    path_filter : show/apply only files whose path contains this substring

    Returns
    -------
    List[FileReport]
    """
    opts = options or ApplyOptions()

    if isinstance(patches, str):
        file_patches = _parse_unified_diff_multifile(patches)
    else:
        file_patches = []
        for p in patches:
            file_patches.append(_parse_one_file_patch_dict(p))

    if path_filter:
        file_patches = [fp for fp in file_patches if path_filter in fp.path]

    reports: List[FileReport] = []
    for fp in file_patches:
        reports.append(_apply_file_patch(fp, opts))

    return reports


def apply_unified_diff_text(
    diff_text: str,
    *,
    options: ApplyOptions | None = None,
    path_filter: Optional[str] = None,
) -> List[FileReport]:
    """
    Convenience wrapper for a single unified diff text that may include several files.
    """
    return apply_patches(diff_text, options=options, path_filter=path_filter)


# ---------- Core per-file apply ----------

def _apply_file_patch(fp: FilePatch, opts: ApplyOptions) -> FileReport:
    repo_root = pathlib.Path(opts.repo_root)
    path = (repo_root / fp.path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        raw = path.read_bytes() if path.exists() else b""
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return FileReport(
            path=fp.path,
            applied=False,
            changed_lines=0,
            hunk_reports=[],
            notes=f"Read error: {e}",
        )

    if opts.normalize_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")

    old_lines = text.splitlines(keepends=True)
    original_lines = list(old_lines)

    # Apply hunks one by one, searching anchors anew each time (robust to shifts)
    h_reports: List[HunkReport] = []
    for idx, hunk in enumerate(fp.hunks):
        r = _apply_single_hunk(
            old_lines,
            hunk,
            ignore_space=opts.ignore_space,
            ratio_cutoff=opts.ratio_cutoff,
        )
        h_reports.append(HunkReport(
            index=idx,
            applied=r.applied,
            already_applied=r.already_applied,
            exact_match=r.exact,
            ratio=r.ratio,
            notes=r.notes,
        ))

    # Build report
    changed = old_lines != original_lines
    changed_lines = _count_changed_lines(original_lines, old_lines)

    preview_text: Optional[str] = None
    if opts.generate_preview:
        preview_text = _unified_preview(original_lines, old_lines, fp.path)

    # Write if needed and not dry-run
    notes = ""
    if changed and not opts.dry_run:
        try:
            _ensure_backup(opts.backups_dir, fp.path, "".join(original_lines))
            path.write_text("".join(old_lines), encoding="utf-8")
        except Exception as e:
            notes = f"Write error: {e}"
            # If write failed, reflect that nothing was actually changed on disk
            return FileReport(
                path=fp.path,
                applied=False,
                changed_lines=0,
                hunk_reports=h_reports,
                notes=notes,
                preview_diff=preview_text,
            )

    return FileReport(
        path=fp.path,
        applied=changed,
        changed_lines=changed_lines,
        hunk_reports=h_reports,
        notes=notes,
        preview_diff=preview_text,
    )


# ---------- Hunk application helpers ----------

@dataclass
class _HunkApplyOutcome:
    applied: bool
    already_applied: bool
    exact: bool
    ratio: float
    notes: str = ""

def _apply_single_hunk(
    lines: List[str],
    hunk: Hunk,
    *,
    ignore_space: bool,
    ratio_cutoff: float,
) -> _HunkApplyOutcome:
    """
    Try to splice the hunk into `lines` in-place.

    Strategy:
      - Construct old_chunk = context+removals (strip '+' lines)
      - Construct new_chunk = context+additions (strip '-' lines)
      - First try exact subsequence match of old_chunk in current `lines`
      - If not found, treat "already-applied" as exact match of new_chunk
      - If still not found, fuzzy search with difflib over context+removals
    """

    # Extract chunks from hunk lines
    old_chunk = [l[1:] for l in hunk.lines if l.startswith((" ", "-"))]
    new_chunk = [l[1:] for l in hunk.lines if l.startswith((" ", "+"))]

    # Normalize for optional whitespace-insensitive matching
    def norm(seq: List[str]) -> List[str]:
        if ignore_space:
            return [re.sub(r"[ \t]+", " ", x.rstrip()) + ("\n" if x.endswith("\n") else "") for x in seq]
        return seq

    n_lines = norm(lines)
    n_old_chunk = norm(old_chunk)
    n_new_chunk = norm(new_chunk)

    # 1) Exact old_chunk match
    pos = _find_subsequence(n_lines, n_old_chunk)
    if pos is not None:
        i, j = pos
        # splice
        lines[i:j] = new_chunk
        return _HunkApplyOutcome(applied=True, already_applied=False, exact=True, ratio=1.0)

    # 2) Already-applied check (new_chunk already present)
    pos_new = _find_subsequence(n_lines, n_new_chunk)
    if pos_new is not None:
        return _HunkApplyOutcome(applied=False, already_applied=True, exact=True, ratio=1.0, notes="already applied")

    # 3) Fuzzy anchor: scan windows near old_chunk length with difflib
    #    We'll try to find the best slice with highest ratio >= cutoff
    best_ratio = -1.0
    best_pos: Optional[Tuple[int, int]] = None
    target_len = max(1, len(n_old_chunk))
    slack = min(12, max(3, target_len // 3))  # modest window slack

    # We constrain window sizes near target length to reduce false positives
    candidate_ranges = []
    for start in range(0, len(n_lines) - max(1, target_len - slack) + 1):
        for length in range(max(1, target_len - slack), min(len(n_lines) - start, target_len + slack) + 1):
            candidate_ranges.append((start, start + length))

    # Quick heuristic: use only every Nth candidate for large files to limit cost
    step = max(1, len(candidate_ranges) // 2000)
    for idx, (i, j) in enumerate(candidate_ranges[::step]):
        window = "".join(n_lines[i:j])
        ref = "".join(n_old_chunk)
        ratio = difflib.SequenceMatcher(a=window, b=ref).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = (i, j)

    if best_pos and best_ratio >= ratio_cutoff:
        i, j = best_pos
        # splice window → new_chunk
        lines[i:j] = new_chunk
        return _HunkApplyOutcome(
            applied=True,
            already_applied=False,
            exact=False,
            ratio=best_ratio,
            notes=f"fuzzy match at {i}:{j}",
        )

    return _HunkApplyOutcome(
        applied=False,
        already_applied=False,
        exact=False,
        ratio=best_ratio if best_ratio >= 0 else 0.0,
        notes="no suitable anchor",
    )


def _find_subsequence(haystack: List[str], needle: List[str]) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) if 'needle' appears as a contiguous slice in 'haystack'.
    Exact match function (after normalization already applied by caller).
    """
    n = len(needle)
    if n == 0:
        return 0, 0
    if n > len(haystack):
        return None
    # KMP would be faster; for simplicity just scan
    for i in range(0, len(haystack) - n + 1):
        if haystack[i:i+n] == needle:
            return i, i+n
    return None


def _count_changed_lines(before: List[str], after: List[str]) -> int:
    diff = difflib.ndiff(before, after)
    return sum(1 for d in diff if d.startswith(("+ ", "- ")))


def _unified_preview(before: List[str], after: List[str], relpath: str) -> str:
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{relpath}",
            tofile=f"b/{relpath}",
            n=3,
        )
    )


def _ensure_backup(backup_dir: str, relpath: str, original_text: str) -> None:
    # Store backups alongside in .bones/backups/<relpath>.bak.<ts>
    root = pathlib.Path(backup_dir) / pathlib.Path(relpath).parent
    root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    bpath = root / (pathlib.Path(relpath).name + f".bak.{ts}")
    try:
        bpath.write_text(original_text, encoding="utf-8")
    except Exception:
        # Backups are best-effort; do not crash patching if backup fails.
        pass


# ---------- Unified diff parsing ----------

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

def _parse_unified_diff_multifile(text: str) -> List[FilePatch]:
    """
    Very small parser for a multi-file unified diff.
    Assumes standard '--- a/path' / '+++ b/path' headers before hunks.
    """
    lines = text.splitlines(keepends=False)

    file_patches: List[FilePatch] = []
    cur: Optional[FilePatch] = None
    pending_hunk: Optional[Hunk] = None

    def finish_hunk():
        nonlocal pending_hunk
        if pending_hunk is not None and cur is not None:
            cur.hunks.append(pending_hunk)
        pending_hunk = None

    def start_file(path: str):
        nonlocal cur
        if cur is not None:
            finish_hunk()
            file_patches.append(cur)
        cur = FilePatch(path=path)

    i = 0
    cur_new_path: Optional[str] = None
    while i < len(lines):
        line = lines[i]

        if line.startswith("--- "):
            # Expect paired +++ on next lines (maybe with timestamps)
            # Format: --- a/path
            i += 1
            # Find +++ line
            while i < len(lines) and not lines[i].startswith("+++ "):
                i += 1
            if i >= len(lines):
                break
            plus = lines[i]
            # Extract path after +++
            # Common formats: +++ b/path or +++ /dev/null
            new_path = plus[4:].strip()
            # Remove possible "a/" or "b/" prefixes
            if new_path.startswith("a/") or new_path.startswith("b/"):
                new_path = new_path[2:]
            if new_path == "/dev/null":
                # deletion only; still set a synthetic path if we saw a prior '---'
                # for MVP, skip deletions without a valid new path
                new_path = None  # type: ignore
            cur_new_path = new_path
            if new_path:
                start_file(new_path)
            i += 1
            continue

        m = _HUNK_RE.match(line)
        if m:
            if cur is None or cur_new_path is None:
                # Missing headers; bail out gracefully
                i += 1
                continue
            finish_hunk()
            old_start = int(m.group("old_start"))
            old_count = int(m.group("old_count") or "1")
            new_start = int(m.group("new_start"))
            new_count = int(m.group("new_count") or "1")
            pending_hunk = Hunk(old_start, old_count, new_start, new_count, [])
            i += 1
            # Collect hunk lines until next hunk/file header
            while i < len(lines):
                l = lines[i]
                if l.startswith(("--- ", "+++ ")) or _HUNK_RE.match(l):
                    break
                if l and l[0] in (" ", "+", "-"):
                    pending_hunk.lines.append(l)
                elif l == r"\ No newline at end of file":
                    # Ignore marker for MVP
                    pass
                else:
                    # Non-prefixed line in hunk: treat as context (safety)
                    pending_hunk.lines.append(" " + l)
                i += 1
            # Don't increment i here; outer loop will continue from current header
            continue

        i += 1

    # finalize
    finish_hunk()
    if cur is not None:
        file_patches.append(cur)

    return file_patches


def _parse_one_file_patch_dict(p: Dict[str, str]) -> FilePatch:
    """
    Accept {"path": "...", "unified_diff": "..."} and parse hunks within.
    If the diff lacks ---/+++ headers (many LLM outputs do), synthesize a header.
    """
    path = p.get("path") or ""
    udiff = p.get("unified_diff") or ""

    # If the diff already includes headers for a different path, we’ll trust them
    if udiff.startswith("--- "):
        fps = _parse_unified_diff_multifile(udiff)
        # Find the one that matches our provided path, else fallback to first
        for fp in fps:
            if fp.path == path or not path:
                return fp
        return fps[0] if fps else FilePatch(path=path, hunks=[])

    # Synthesize minimal headers if missing
    fake = f"--- a/{path}\n+++ b/{path}\n{udiff}"
    fps = _parse_unified_diff_multifile(fake)
    return fps[0] if fps else FilePatch(path=path, hunks=[])

# ---------- End ----------
