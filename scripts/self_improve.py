#!/usr/bin/env python3
"""
Hermes self-improvement loop — safe by construction.

Two tiers, matching "auto-apply low-risk, propose the rest":

  TIER 1  (--auto)  low-risk, deterministic, reversible → APPLIED automatically:
    - refresh free OpenRouter models   (discover_openrouter_free.py)
    - prune dead / demote throttled    (health_check.py)
    - re-rank chain by capability      (rank_models.py)
    Each already backs up config and validates. No LLM, no source/core edits.

  TIER 2  (--propose)  creative/uncertain → RESEARCHED and written to a report
    you approve (never auto-applied). Uses Hermes itself (`hermes -z`) so it can
    web-research proven patterns from comparable agent projects, then proposes
    prioritized, verifiable changes across: chain/routing, prompts/reasoning,
    quality (verify+learn), and skills. Output → ~/.hermes/PROPOSALS.md.

Never edits Hermes's own source or config-core unattended. That is the whole
point — the old auto_improve daemon did exactly that and clobbered the chain.

Usage:
    python3 scripts/self_improve.py            # auto (tier 1) then propose (tier 2)
    python3 scripts/self_improve.py --auto      # tier 1 only
    python3 scripts/self_improve.py --propose    # tier 2 only
    python3 scripts/self_improve.py --dry-run    # show what tier 1 would do
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
# Windows-safe console: cp1252 terminals can't encode glyphs like the medical
# staff / check marks; force UTF-8 so prints never crash on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
LOG = HERMES_HOME / "self_improve_log.md"
PROPOSALS = HERMES_HOME / "PROPOSALS.md"
C, G, Y, DIM, RST = "\033[36m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def _run(script: str, args: list[str]) -> str:
    """Run a sibling script with the current interpreter; return trimmed stdout."""
    cmd = [sys.executable, str(REPO / "scripts" / script), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return f"(failed to run {script}: {exc})"


def tier1_auto(dry: bool) -> list[str]:
    """Low-risk deterministic maintenance — applied automatically."""
    summary = []
    flag = ["--dry-run"] if dry else []
    print(f"{C}▸ Tier 1: low-risk auto-maintenance{RST}")
    steps = [
        ("discover_openrouter_free.py", [] if not dry else ["--dry-run"], "refresh free OpenRouter models"),
        ("health_check.py", flag + ["--reseed"], "re-test known providers + prune dead"),
        ("rank_models.py", flag, "re-rank chain by capability"),
    ]
    for script, a, label in steps:
        print(f"  {DIM}- {label}…{RST}")
        out = _run(script, a)
        # capture the last meaningful line as the result
        lines = [ln for ln in out.splitlines() if ln.strip()]
        tail = next((ln for ln in reversed(lines)
                     if any(s in ln for s in ("✓", "added", "updated", "reordered",
                                              "optimal", "unchanged", "already"))), lines[-1] if lines else "")
        # strip ANSI for the log
        import re as _re
        tail = _re.sub(r"\033\[[0-9;]*m", "", tail).strip()
        summary.append(f"- **{label}**: {tail}")
        print(f"    {G}{tail}{RST}")
    return summary


def _context_for_proposals() -> str:
    """Gather current state so the proposer has grounding."""
    parts = []
    cfg = HERMES_HOME / "config.yaml"
    if cfg.exists():
        import yaml
        c = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        model = (c.get("model") or {})
        fb = c.get("fallback_providers") or []
        parts.append(f"Primary: {model.get('provider')}/{model.get('default')}")
        parts.append("Chain: " + ", ".join(f"{e.get('provider')}/{e.get('model')}" for e in fb))
    if LOG.exists():
        parts.append("Recent auto-maintenance:\n" + LOG.read_text(encoding="utf-8")[-1500:])
    return "\n".join(parts)


PROPOSE_PROMPT = """You are improving the Hermes agent system you run on. Produce a concrete, \
prioritized SELF-IMPROVEMENT REPORT — do NOT change any files; only propose.

Current state:
{context}

Method (important):
1. Pick 3-6 concrete pain points or opportunities.
2. For each, RESEARCH how comparable open agent projects / papers solve it (use \
web_search / web_extract). Prefer adapting a PROVEN pattern over inventing one; cite the source.
3. Give a concrete change: the exact file/command/diff or skill to add.
4. Tag each [AUTO-SAFE] (provider/chain/routing/capability-score tweak applied via the \
existing backup+validate scripts) or [NEEDS-REVIEW] (skills, prompts, source, anything else).
5. State how to VERIFY it helped (an eval prompt, a metric, a check). Reject ideas you can't verify.

Cover these areas: (a) model chain & routing, (b) prompts & reasoning-effort/MoA usage, \
(c) response quality via eval + verify + lesson-cards, (d) new skills (proven patterns).

Output GitHub-flavored markdown with one section per proposal: \
### <title> [AUTO-SAFE|NEEDS-REVIEW]  / Problem / Researched pattern (+source) / Change / Verify. \
Be specific and terse. No preamble."""


def tier2_propose() -> None:
    print(f"\n{C}▸ Tier 2: researched proposals (propose-only){RST}")
    hermes = shutil.which("hermes")
    if not hermes:
        print(f"  {Y}hermes not on PATH — skipping proposals.{RST}")
        return
    prompt = PROPOSE_PROMPT.format(context=_context_for_proposals())
    print(f"  {DIM}asking Hermes to research + propose (this uses the chain + web)…{RST}")
    try:
        r = subprocess.run([hermes, "-z", prompt], capture_output=True, text=True, timeout=1200)
        report = (r.stdout or "").strip()
    except Exception as exc:
        print(f"  {Y}proposal run failed: {exc}{RST}")
        return
    if not report:
        print(f"  {Y}no proposal output (chain may be rate-limited — retry later).{RST}")
        return
    stamp = time.strftime("%Y-%m-%d %H:%M")
    header = f"# Hermes self-improvement proposals\n_Generated {stamp}. Review before applying; nothing was changed._\n\n"
    PROPOSALS.write_text(header + report + "\n", encoding="utf-8")
    print(f"  {G}✓ wrote {PROPOSALS}{RST}  {DIM}(review, then apply what you like){RST}")


def main() -> int:
    dry = "--dry-run" in sys.argv
    only_auto = "--auto" in sys.argv
    only_propose = "--propose" in sys.argv
    do_auto = only_auto or not only_propose
    do_propose = only_propose or not only_auto

    print(f"\n{C}⚕ Hermes self-improvement{RST} {DIM}(auto low-risk, propose the rest){RST}")
    log_lines = []
    if do_auto:
        log_lines = tier1_auto(dry)
    if do_propose and not dry:
        tier2_propose()

    if do_auto and not dry:
        HERMES_HOME.mkdir(parents=True, exist_ok=True)
        entry = f"\n## {time.strftime('%Y-%m-%d %H:%M')} — auto-maintenance\n" + "\n".join(log_lines) + "\n"
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(entry)
        print(f"\n{G}✓ logged to {LOG}{RST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
