#!/usr/bin/env python3
"""
Discover currently-FREE OpenRouter models and add them to the fallback chain.

Queries OpenRouter's live /models API (not web scraping), keeps the models that
are actually free right now (prompt + completion price == 0) with enough context,
and inserts them into config.yaml's fallback_providers. This gives the chain many
free options so a 429 on one rolls straight to the next.

Safe by design:
  - validates against the live API (a model in the list exists & is free today),
  - backs up config.yaml first,
  - never changes your primary model or non-OpenRouter providers,
  - keeps the local/OAuth safety net (ollama, codex) last,
  - dedupes.

Usage:
    python3 scripts/discover_openrouter_free.py                 # add to chain
    python3 scripts/discover_openrouter_free.py --dry-run       # just list
    python3 scripts/discover_openrouter_free.py --limit 20      # cap (default 15)
    python3 scripts/discover_openrouter_free.py --min-context 32000
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Run via the Hermes venv python or: uv run --with pyyaml python ...")

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CFG = HERMES_HOME / "config.yaml"
API = "https://openrouter.ai/api/v1/models"
G, Y, C, DIM, RST = "\033[32m", "\033[33m", "\033[36m", "\033[2m", "\033[0m"


def load_key() -> str:
    env = dict(os.environ)
    envf = HERMES_HOME / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env.get("OPENROUTER_API_KEY", "")


def fetch_free_models(key: str, min_context: int, limit: int) -> list[dict]:
    req = urllib.request.Request(API, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    out = []
    for m in data.get("data", []):
        pricing = m.get("pricing") or {}
        try:
            free = float(pricing.get("prompt", "1")) == 0.0 and float(pricing.get("completion", "1")) == 0.0
        except (TypeError, ValueError):
            free = False
        if not free:
            continue
        ctx = int(m.get("context_length") or 0)
        if ctx < min_context:
            continue
        # Must OUTPUT text (drop text->image / text->audio models like Lyria).
        arch = m.get("architecture") or {}
        out_mods = arch.get("output_modalities")
        if out_mods:
            outs = [str(x).lower() for x in out_mods]
            # chat models output text ONLY; drop generators (audio/image out).
            if "text" not in outs or "audio" in outs or "image" in outs:
                continue
        else:
            modality = (arch.get("modality") or "").lower()   # e.g. "text->text", "text->audio"
            tail = modality.split("->", 1)[1] if "->" in modality else modality
            if "text" not in tail or "audio" in tail or "image" in tail:
                continue
        # Skip auto-routers / meta models (an empty routed model is what caused
        # the "No models provided" 400 in the first place).
        low = m["id"].lower()
        if "auto" in low or low in ("openrouter/free", "openrouter/default"):
            continue
        out.append({"id": m["id"], "ctx": ctx, "name": m.get("name", m["id"])})
    # strongest context first, then stable by id
    out.sort(key=lambda x: (-x["ctx"], x["id"]))
    return out[:limit]


def main() -> int:
    dry = "--dry-run" in sys.argv
    limit = 15
    min_context = 16000
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if "--min-context" in sys.argv:
        min_context = int(sys.argv[sys.argv.index("--min-context") + 1])

    key = load_key()
    if not key:
        sys.exit("OPENROUTER_API_KEY not found in ~/.hermes/.env")

    print(f"\n{C}⚕ discovering free OpenRouter models{RST} (min ctx {min_context:,}, top {limit})\n")
    try:
        models = fetch_free_models(key, min_context, limit)
    except Exception as exc:
        sys.exit(f"failed to query OpenRouter: {exc}")

    if not models:
        print(f"{Y}No free models matched. Try --min-context 8000.{RST}")
        return 1
    for m in models:
        print(f"  {G}+{RST} openrouter/{m['id']} {DIM}({m['ctx']:,} ctx){RST}")

    if dry:
        print(f"\n{C}(dry-run){RST} not written.")
        return 0
    if not CFG.exists():
        sys.exit(f"\nno config at {CFG} — run bootstrap first")

    cfg = yaml.safe_load(CFG.read_text()) or {}
    fb = cfg.get("fallback_providers") or []

    SAFETY = {"openai-codex", "custom:ollama"}
    core = [e for e in fb if e.get("provider") not in SAFETY and e.get("provider") != "openrouter"]
    safety = [e for e in fb if e.get("provider") in SAFETY]
    discovered = [{"provider": "openrouter", "model": m["id"]} for m in models]

    # non-OpenRouter free providers first (reliable), then the free OpenRouter
    # pool (deep failover), then the local/OAuth safety net.
    seen, merged = set(), []
    for e in core + discovered + safety:
        keyid = f"{e.get('provider')}/{e.get('model')}"
        if keyid not in seen:
            seen.add(keyid)
            merged.append(e)

    bak = CFG.with_name(f"config.yaml.bak.orfree-{time.strftime('%Y%m%d-%H%M%S')}")
    bak.write_text(CFG.read_text())
    cfg["fallback_providers"] = merged
    CFG.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=1000))
    print(f"\n{G}✓ added {len(discovered)} free OpenRouter models{RST} "
          f"(chain now {len(merged)} fallbacks). backup: {bak.name}")
    print(f"{DIM}Primary model unchanged. Re-run anytime to refresh the free list.{RST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
