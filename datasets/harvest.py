#!/usr/bin/env python3
"""Rerunnable training-data harvest over Forge's own verified work.

Reads (read-only):
  - git history (subprocess: git log / git show)
  - worker/runs/<id>/ (the local-worker dispatch protocol's own records)
  - projects/*/design/build-plan.json and design/*.md logs
  - projects/*/renders/* and design/refs-manifest.json (or design/refs/)

Writes (full rewrite each run, never a merge/append):
  - datasets/code_pairs.jsonl
  - datasets/worker_runs.jsonl
  - datasets/recipes.jsonl
  - datasets/render_judgments.jsonl

stdlib only. See datasets/SCHEMA.md for the record shapes and every
truncation cap used below. Idempotent: re-running replaces these four
files and touches nothing else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = Path(__file__).resolve().parent

# ---- caps (documented in SCHEMA.md) ----------------------------------
COMMIT_DIFF_CAP = 20_000
ATTEMPT_DIFF_CAP = 12_000
GATE_LOG_CAP = 4_000
DESIGN_LOG_SECTION_CAP = 3_000
VERDICT_PARAGRAPH_CAP = 1_500
DOCS_RECIPE_CAP = 4_000


def cap(text: str, limit: int) -> tuple[str, int, bool]:
    """Truncate text to limit chars. Returns (text, full_chars, truncated)."""
    full = len(text)
    if full <= limit:
        return text, full, False
    return text[:limit] + "\n... [truncated]", full, True


def rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")


def run_git(args: list[str]) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return out.stdout


# =======================================================================
# 1. code_pairs.jsonl — one record per commit
# =======================================================================

_SEP = "\x1e"  # ASCII record separator, unlikely in commit text
_LOG_FMT = _SEP.join(["%H", "%h", "%an", "%aI", "%s", "%b"]) + "\x1d"


def harvest_code_pairs() -> list[dict]:
    raw = run_git(["log", f"--format={_LOG_FMT}"])
    entries = [e for e in raw.split("\x1d") if e.strip("\n")]
    records = []
    for entry in entries:
        entry = entry.lstrip("\n")
        parts = entry.split(_SEP)
        if len(parts) < 6:
            continue
        full_sha, short_sha, author, date, subject, body = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5].strip("\n")
        )
        instruction = subject if not body else f"{subject}\n\n{body}"

        diff_raw = run_git(["show", "--no-color", "-U3", "--format=", full_sha])
        diff_text, diff_full, diff_trunc = cap(diff_raw, COMMIT_DIFF_CAP)

        stat_raw = run_git(
            ["show", "--no-color", "--format=", "--numstat", full_sha]
        )
        files_changed = []
        insertions = deletions = 0
        for line in stat_raw.splitlines():
            line = line.strip()
            if not line:
                continue
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            ins, dele, fname = cols
            files_changed.append(fname)
            if ins.isdigit():
                insertions += int(ins)
            if dele.isdigit():
                deletions += int(dele)

        verdict = "PASS"
        if re.search(r"\b(revert|rollback)\b", subject, re.IGNORECASE):
            verdict = "ROLLBACK"

        records.append({
            "id": f"code_pairs:{short_sha}",
            "instruction": instruction,
            "output": diff_text,
            "verdict": verdict,
            "meta": {
                "sha": full_sha,
                "short_sha": short_sha,
                "author": author,
                "date": date,
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
                "diff_chars_full": diff_full,
                "diff_truncated": diff_trunc,
                "verdict_note": (
                    "implicit — every commit on this branch is gated before "
                    "commit per docs/lane-conventions.md; ROLLBACK is a heuristic "
                    "hit on the commit subject containing 'revert' or 'rollback', "
                    "not a parsed gate log"
                ),
            },
        })
    return records


# =======================================================================
# 2. worker_runs.jsonl — one record per worker/runs/<id>/
# =======================================================================

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def harvest_worker_runs() -> list[dict]:
    runs_dir = REPO_ROOT / "worker" / "runs"
    if not runs_dir.is_dir():
        return []

    records = []
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        if ".stale-" in run_dir.name:
            continue
        result_path = run_dir / "result.json"
        brief_path = run_dir / "brief.md"
        if not result_path.is_file():
            continue

        try:
            result = json.loads(_read_text(result_path) or "{}")
        except json.JSONDecodeError:
            result = {}

        instruction = _read_text(brief_path).strip()
        run_id = result.get("id", run_dir.name)
        status = result.get("status", "UNKNOWN")

        attempt_records = []
        output_chunks = []
        for attempt in result.get("attempts", []):
            n = attempt.get("attempt")
            attempt_dir = run_dir / f"attempt-{n}"
            diff_raw = _read_text(attempt_dir / "changes.diff")
            diff_text, diff_full, diff_trunc = cap(diff_raw, ATTEMPT_DIFF_CAP)
            gate_raw = _read_text(attempt_dir / "gate.log")
            gate_text, gate_full, gate_trunc = cap(gate_raw, GATE_LOG_CAP)
            passed = bool(attempt.get("passed"))

            attempt_records.append({
                "attempt": n,
                "passed": passed,
                "changed_files": attempt.get("changed_files", []),
                "diff_chars_full": diff_full,
                "diff_truncated": diff_trunc,
                "diff": diff_text,
                "gate_log_chars_full": gate_full,
                "gate_log_truncated": gate_trunc,
                "gate_log": gate_text,
            })
            output_chunks.append(
                f"=== attempt {n} (passed={passed}) ===\n{diff_text}\n"
                f"--- gate ---\n{gate_text}"
            )

        records.append({
            "id": f"worker_runs:{run_id}",
            "instruction": instruction,
            "output": "\n\n".join(output_chunks),
            "verdict": status,
            "meta": {
                "run_id": run_id,
                "backend": result.get("backend"),
                "model": result.get("model"),
                "attempts_used": result.get("attempts_used", len(attempt_records)),
                "max_attempts": result.get("max_attempts"),
                "files_scope": result.get("files_scope", []),
                "acceptance_cmd": result.get("acceptance_cmd", ""),
                "escalate": result.get("escalate", False),
                "escalation_note": result.get("escalation_note", ""),
                "attempts": attempt_records,
            },
        })
    return records


# =======================================================================
# 3. recipes.jsonl — WHEN/RECIPE/WHY, from docs/recipes/*.md when it
#    exists, else (fallback) from build-plan.json + project design/*.md
# =======================================================================

_HEADING_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)
_OVERALL_RE = re.compile(r"Overall:\s*(PASSED|FAILED)", re.IGNORECASE)
_H1_RE = re.compile(r"^#\s+(.*)$", re.MULTILINE)


def _parse_h2_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    headings = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(headings):
        name = m.group(1).strip().upper()
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _docs_recipe_records(md_path: Path) -> list[dict]:
    """One accepted-recipe record, plus one rejected-alternatives record
    when present, per docs/recipes/<name>.md — these files already carry
    the WHEN/THE RECIPE/WHY/REJECTED/SOURCE structure natively."""
    text = _read_text(md_path)
    if not text.strip():
        return []
    title_m = _H1_RE.search(text)
    title = title_m.group(1).strip() if title_m else md_path.stem
    sections = _parse_h2_sections(text)
    when_text = sections.get("WHEN", "")
    why_text = sections.get("WHY", "")
    recipe_text = sections.get("THE RECIPE", sections.get("RECIPE", ""))
    rejected_text = sections.get("REJECTED", "")
    source_text = sections.get("SOURCE", "")

    out = []
    recipe_capped, recipe_full, recipe_trunc = cap(recipe_text, DOCS_RECIPE_CAP)
    out.append({
        "id": f"recipes:docs:{md_path.stem}:recipe",
        "when": when_text,
        "recipe": recipe_capped,
        "why": why_text,
        "verdict": "accepted",
        "meta": {
            "source_type": "docs_recipe",
            "project": None,
            "title": title,
            "source_file": rel(md_path),
            "source_citations": source_text,
            "recipe_chars_full": recipe_full,
            "recipe_truncated": recipe_trunc,
        },
    })
    if rejected_text.strip():
        rej_capped, rej_full, rej_trunc = cap(rejected_text, DOCS_RECIPE_CAP)
        out.append({
            "id": f"recipes:docs:{md_path.stem}:rejected",
            "when": when_text,
            "recipe": rej_capped,
            "why": (
                "rejected alternative(s) — tried and abandoned; see the "
                "sibling ':recipe' record (same 'when') for what was used "
                "instead"
            ),
            "verdict": "rejected",
            "meta": {
                "source_type": "docs_recipe_rejected",
                "project": None,
                "title": title,
                "source_file": rel(md_path),
                "source_citations": source_text,
                "recipe_chars_full": rej_full,
                "recipe_truncated": rej_trunc,
            },
        })
    return out


def _build_plan_records(project: str, design_dir: Path) -> list[dict]:
    bp_path = design_dir / "build-plan.json"
    if not bp_path.is_file():
        return []
    try:
        bp = json.loads(_read_text(bp_path) or "{}")
    except json.JSONDecodeError:
        return []

    out = []
    for stage in bp.get("stages", []):
        status = stage.get("status", "pending")
        if status == "pending":
            continue
        stage_id = stage.get("id", "?")
        title = stage.get("title", stage_id)
        does = stage.get("does", "")
        recipe_obj = {
            "numbers": stage.get("numbers", {}),
            "artifacts": stage.get("artifacts", []),
        }
        out.append({
            "id": f"recipes:{project}:{stage_id}",
            "when": f"{project} / stage {stage_id} — {title}",
            "recipe": recipe_obj,
            "why": does,
            "verdict": status,
            "meta": {
                "source_type": "build_plan_stage",
                "project": project,
                "source_file": rel(bp_path),
                "recipe_chars_full": None,
                "recipe_truncated": None,
            },
        })
    return out


def _design_log_records(project: str, design_dir: Path) -> list[dict]:
    out = []
    for md_path in sorted(design_dir.glob("*.md")):
        if md_path.name in ("requirements.md",):
            continue
        text = _read_text(md_path)
        if not text.strip():
            continue

        headings = list(_HEADING_RE.finditer(text))
        if not headings:
            sections = [(md_path.stem, text)]
        else:
            sections = []
            for i, m in enumerate(headings):
                heading = m.group(1).strip()
                start = m.end()
                end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
                sections.append((heading, text[start:end].strip()))

        for idx, (heading, body) in enumerate(sections):
            if not body.strip():
                continue
            body_text, body_full, body_trunc = cap(body, DESIGN_LOG_SECTION_CAP)

            verdict = "unlabeled"
            m = _OVERALL_RE.search(heading) or _OVERALL_RE.search(body)
            if m:
                verdict = "passed" if "PASS" in m.group(1).upper() else "failed"
            elif re.search(r"\bPASS(ED)?\b", heading, re.IGNORECASE) and not re.search(
                r"\bFAIL", heading, re.IGNORECASE
            ):
                verdict = "passed"
            elif re.search(r"\bFAIL(ED)?\b", heading, re.IGNORECASE) and not re.search(
                r"\bPASS", heading, re.IGNORECASE
            ):
                verdict = "failed"

            out.append({
                "id": f"recipes:{project}:{md_path.stem}:{idx}",
                "when": f"{project} / {md_path.name} — {heading}",
                "recipe": body_text,
                "why": heading,
                "verdict": verdict,
                "meta": {
                    "source_type": "design_log",
                    "project": project,
                    "source_file": rel(md_path),
                    "recipe_chars_full": body_full,
                    "recipe_truncated": body_trunc,
                },
            })
    return out


def harvest_recipes() -> list[dict]:
    docs_recipes_dir = REPO_ROOT / "docs" / "recipes"
    if docs_recipes_dir.is_dir():
        md_files = sorted(
            p for p in docs_recipes_dir.glob("*.md") if p.name.upper() != "INDEX.MD"
        )
        if md_files:
            records = []
            for md_path in md_files:
                records.extend(_docs_recipe_records(md_path))
            return records

    # Fallback: docs/recipes doesn't exist (or is empty) yet — read the
    # WHEN/RECIPE/WHY structure directly out of the project logs instead.
    projects_dir = REPO_ROOT / "projects"
    if not projects_dir.is_dir():
        return []
    records = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        design_dir = project_dir / "design"
        if not design_dir.is_dir():
            continue
        project = project_dir.name
        records.extend(_build_plan_records(project, design_dir))
        records.extend(_design_log_records(project, design_dir))
    return records


# =======================================================================
# 4. render_judgments.jsonl — one record per render file
# =======================================================================

_RENDER_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".gif"}


def _refs_for_project(project_dir: Path) -> tuple[list[str], str]:
    manifest_path = project_dir / "design" / "refs-manifest.json"
    refs_dir = project_dir / "design" / "refs"
    if manifest_path.is_file():
        try:
            manifest = json.loads(_read_text(manifest_path) or "[]")
        except json.JSONDecodeError:
            manifest = []
        paths = []
        for entry in manifest:
            fname = entry.get("file") if isinstance(entry, dict) else None
            if fname:
                paths.append(rel(refs_dir / fname))
        if paths:
            return paths, "refs_manifest"
    if refs_dir.is_dir():
        paths = sorted(rel(p) for p in refs_dir.iterdir() if p.is_file())
        if paths:
            return paths, "refs_dir_listing"
    return [], "none"


def _find_verdict_for_render(project_dir: Path, render_name: str) -> tuple[str | None, str | None]:
    design_dir = project_dir / "design"
    if not design_dir.is_dir():
        return None, None
    for md_path in sorted(design_dir.glob("*.md")):
        text = _read_text(md_path)
        if render_name not in text:
            continue
        # containing paragraph: split on blank lines, find the one with the match
        paragraphs = re.split(r"\n\s*\n", text)
        for para in paragraphs:
            if render_name in para:
                return para.strip(), rel(md_path)
    return None, None


def harvest_render_judgments() -> list[dict]:
    projects_dir = REPO_ROOT / "projects"
    if not projects_dir.is_dir():
        return []
    records = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        renders_dir = project_dir / "renders"
        if not renders_dir.is_dir():
            continue
        project = project_dir.name
        reference_paths, reference_source = _refs_for_project(project_dir)

        for render_path in sorted(renders_dir.iterdir()):
            if not render_path.is_file():
                continue
            if render_path.suffix.lower() not in _RENDER_EXTS:
                continue
            verdict_para, verdict_file = _find_verdict_for_render(
                project_dir, render_path.name
            )
            if verdict_para is not None:
                verdict_text, verdict_full, verdict_trunc = cap(
                    verdict_para, VERDICT_PARAGRAPH_CAP
                )
                verdict = "labeled"
            else:
                verdict_text, verdict_full, verdict_trunc = None, None, None
                verdict = "unlabeled"

            records.append({
                "id": f"render_judgments:{project}:{render_path.name}",
                "render_path": rel(render_path),
                "reference_paths": reference_paths,
                "reference_source": reference_source,
                "verdict_text": verdict_text,
                "verdict": verdict,
                "meta": {
                    "project": project,
                    "verdict_source_file": verdict_file,
                    "verdict_chars_full": verdict_full,
                    "verdict_truncated": verdict_trunc,
                },
            })
    return records


# =======================================================================
# main
# =======================================================================

def main() -> int:
    DATASETS_DIR.mkdir(exist_ok=True)

    corpora = [
        ("code_pairs.jsonl", harvest_code_pairs),
        ("worker_runs.jsonl", harvest_worker_runs),
        ("recipes.jsonl", harvest_recipes),
        ("render_judgments.jsonl", harvest_render_judgments),
    ]

    total = 0
    for filename, fn in corpora:
        records = fn()
        write_jsonl(DATASETS_DIR / filename, records)
        print(f"{filename}: {len(records)} record(s)")
        total += len(records)
    print(f"total: {total} record(s) across {len(corpora)} corpora")
    return 0


if __name__ == "__main__":
    sys.exit(main())
