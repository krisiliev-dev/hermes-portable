#!/usr/bin/env python3
"""
Rank the fallback chain by CAPABILITY (most capable first), not just health.

Blends three signals into one score per model and reorders fallback_providers:
  - capability : quality prior from config/capability_scores.yaml (helpful /
                 logical / high-quality), with a size/type heuristic for models
                 not in the table.
  - reliability: from the last health check (.portable_health.json) — live=1.0,
                 throttled=0.5, dead=dropped.
  - speed      : from the health check's measured latency (faster = higher).

Two modes:
  hermes --rank             benchmark-based: instant, zero API calls.
  hermes --rank --eval      also run a few live prompts per model (correctness +
                            latency) and blend the measured result in. Costs API
                            calls and can hit free-tier rate limits mid-run.

The local/OAuth safety net (ollama, codex) always stays last. Primary model is
left alone unless you pass --set-primary (promotes the top-ranked live model).

Usage:
    python3 scripts/rank_models.py [--eval] [--task general|reasoning|coding]
                                   [--dry-run] [--set-primary]
"""
from __future__ import annotations

import json
import os
import re
import sys
# Windows-safe console: cp1252 terminals can't encode glyphs like the medical
# staff / check marks; force UTF-8 so prints never crash on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import time
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Run via the Hermes venv python.")

REPO = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CFG = HERMES_HOME / "config.yaml"
G, Y, C, DIM, RST = "\033[32m", "\033[33m", "\033[36m", "\033[2m", "\033[0m"
SAFETY = {"openai-codex", "custom:ollama"}


def norm(model: str) -> str:
    return (model or "").strip().lower().removesuffix(":free")


def heuristic_caps(model: str, defaults: dict) -> dict:
    """Estimate capability for a model not in the table, from its name."""
    m = model.lower()
    # biggest param count mentioned, in billions
    sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*b(?:\b|-|:)", m)]
    b = max(sizes) if sizes else 0.0
    if b >= 200:   base = 0.90
    elif b >= 100: base = 0.86
    elif b >= 60:  base = 0.82
    elif b >= 30:  base = 0.76
    elif b >= 12:  base = 0.70
    elif b >= 7:   base = 0.63
    elif b > 0:    base = 0.48
    else:          base = float(defaults.get("general", 0.60))
    if any(k in m for k in ("flash", "nano", "mini", "lite", "small", "-xs")):
        base -= 0.05
    reasoning = base + (0.03 if any(k in m for k in ("reason", "think", "-r1", "o1")) else 0)
    coding = base + (0.06 if any(k in m for k in ("code", "coder", "codestral")) else -0.02)
    return {"reasoning": round(min(reasoning, 0.98), 2),
            "coding": round(min(coding, 0.98), 2),
            "general": round(base, 2)}


def capability_for(model: str, table: dict, defaults: dict) -> dict:
    key = norm(model)
    for k, v in table.items():
        if norm(k) == key:
            return v
    # try last path segment (e.g. provider prefix differences)
    tail = key.split("/")[-1]
    for k, v in table.items():
        if norm(k).split("/")[-1] == tail:
            return v
    return heuristic_caps(model, defaults)


def load_health():
    p = HERMES_HOME / ".portable_health.json"
    if not p.exists():
        return {}, set(), set(), set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}, set(), set(), set()
    lat = {(e["provider"], e["model"]): e.get("latency") for e in d.get("live", [])}
    live = set(lat)
    thr = {(e["provider"], e["model"]) for e in d.get("throttled", [])}
    dead = {(e["provider"], e["model"]) for e in d.get("dead", [])}
    return lat, thr, dead, live


def load_env():
    env = dict(os.environ)
    envf = HERMES_HOME / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


# ── live eval (--eval) ─────────────────────────────────────────────────────
EVAL_PROMPTS = [
    # (prompt, checker(answer_text) -> bool)
    ("A bat and ball cost $1.10 total. The bat costs $1.00 more than the ball. "
     "How many cents is the ball? Reply with only the number.",
     lambda a: "5" in re.findall(r"\d+", a)[:1] if re.findall(r"\d+", a) else False),
    ("Reply with exactly this word and nothing else: PINGOK",
     lambda a: "pingok" in a.lower() and len(a.strip()) <= 12),
    ("Reply with ONLY Python code: a function is_even(n) that returns True iff n is even.",
     lambda a: "is_even" in a.lower() and "%2" in a.replace(" ", "")),
]


def _resolve_endpoint(provider, model, cfg, providers_cat, env):
    """Return (base_url, key) for a chain entry, or (None, None) if not pingable."""
    custom = {f"custom:{c['name']}": c for c in (cfg.get("custom_providers") or [])}
    meta = providers_cat.get(provider, {})
    kind = meta.get("kind", "native")
    if kind in ("oauth", "local"):
        return None, None
    if provider in custom:
        c = custom[provider]
        return c.get("base_url"), env.get(c.get("key_env", ""), "")
    return meta.get("base_url"), env.get(meta.get("key_env", ""), "")


def eval_model(base, key, model, timeout=25.0):
    """Return (correctness 0..1, avg_latency_s) or (None, None) on hard failure."""
    correct, lat_sum, n = 0, 0.0, 0
    for prompt, check in EVAL_PROMPTS:
        body = json.dumps({"model": model,
                           "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": 200, "temperature": 0}).encode()
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                     method="POST", headers={"Authorization": f"Bearer {key}",
                                                             "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            lat_sum += time.time() - t0
            n += 1
            text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
            if check(text):
                correct += 1
        except Exception:
            continue
    if n == 0:
        return None, None
    return correct / len(EVAL_PROMPTS), lat_sum / n


def main() -> int:
    dry = "--dry-run" in sys.argv
    do_eval = "--eval" in sys.argv
    set_primary = "--set-primary" in sys.argv
    task = "general"
    if "--task" in sys.argv:
        task = sys.argv[sys.argv.index("--task") + 1]

    if not CFG.exists():
        sys.exit(f"no config at {CFG} — run bootstrap first")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}
    scores_cfg = yaml.safe_load((REPO / "config" / "capability_scores.yaml").read_text(encoding="utf-8"))
    providers_cat = yaml.safe_load((REPO / "providers.yaml").read_text(encoding="utf-8")).get("providers", {})
    table = scores_cfg.get("models", {})
    defaults = scores_cfg.get("defaults", {})
    W = scores_cfg.get("weights", {"capability": 0.6, "reliability": 0.25, "speed": 0.15})
    mix = scores_cfg.get("capability_mix", {}).get(task) or {"general": 0.5, "reasoning": 0.3, "coding": 0.2}
    weak_thr = float(scores_cfg.get("weak_capability_threshold", 0.75))
    lat_map, throttled, dead, live = load_health()
    env = load_env()

    fb = cfg.get("fallback_providers") or []
    core = [e for e in fb if e.get("provider") not in SAFETY]
    safety = [e for e in fb if e.get("provider") in SAFETY]

    max_lat = max([v for v in lat_map.values() if v] or [1.0])
    print(f"\n{C}⚕ ranking chain by capability{RST} (task={task}"
          f"{', +live eval' if do_eval else ''})\n")

    ranked = []
    for e in core:
        p, m = e.get("provider"), e.get("model")
        key = (p, m)
        if key in dead:
            print(f"  {DIM}drop {p}/{m} (health: dead){RST}")
            continue
        caps = capability_for(m, table, defaults)
        cap = sum(caps.get(dim, 0.6) * w for dim, w in mix.items())

        # reliability + speed from health cache
        if key in live:
            reliability = 1.0
        elif key in throttled:
            reliability = 0.5
        else:
            reliability = 0.7  # unknown (health not run for it)
        lat = lat_map.get(key)
        speed = (1.0 - min(lat / max_lat, 1.0)) if lat else 0.5

        measured = None
        if do_eval:
            base, k = _resolve_endpoint(p, m, cfg, providers_cat, env)
            if base and k:
                corr, elat = eval_model(base, k, m)
                if corr is not None:
                    measured = corr
                    if elat:
                        speed = 1.0 - min(elat / 20.0, 1.0)
                    print(f"  {DIM}eval {p}/{m}: correct={corr:.2f} {elat:.1f}s{RST}")

        cap_final = cap if measured is None else (0.5 * cap + 0.5 * measured)
        score = (W["capability"] * cap_final + W["reliability"] * reliability + W["speed"] * speed)
        ranked.append((score, cap_final, reliability, e, m, p))

    ranked.sort(key=lambda x: -x[0])

    # Tier the chain: strong free (cap ≥ threshold) → PAID safety-net (Codex,
    # promoted above weak free = smart-but-paid beats dumb-but-free) → weak free
    # → LOCAL (ollama, always last, offline resort).
    paid = [e for e in safety if e.get("provider") == "openai-codex"]
    local = [e for e in safety if e.get("provider") == "custom:ollama"]
    other_safety = [e for e in safety if e.get("provider") not in ("openai-codex", "custom:ollama")]
    strong = [r for r in ranked if r[1] >= weak_thr]
    weak = [r for r in ranked if r[1] < weak_thr]

    print()
    for score, cap, rel, e, m, p in strong:
        print(f"  {G}{score:.3f}{RST}  {p}/{m}  {DIM}(cap {cap:.2f}, rel {rel:.1f}){RST}")
    for e in paid:
        print(f"  {C}——.—  {e['provider']}/{e['model']}  (paid safety-net → above weak free){RST}")
    for score, cap, rel, e, m, p in weak:
        print(f"  {DIM}{score:.3f}  {p}/{m}  (cap {cap:.2f}, weak → below paid){RST}")
    for e in local + other_safety:
        print(f"  {DIM}—.——  {e['provider']}/{e['model']}  (local/last resort){RST}")

    new_fb = [r[3] for r in strong] + paid + [r[3] for r in weak] + local + other_safety

    changed = new_fb != fb
    if set_primary and strong:
        top = strong[0]
        cur = cfg.get("model", {}) or {}
        if cur.get("provider") != top[5] or cur.get("default") != top[4]:
            print(f"\n{Y}⟳ set primary → {top[5]}/{top[4]}{RST}")
            cfg["model"] = {"provider": top[5], "default": top[4]}
            new_fb = [r[3] for r in strong[1:]] + paid + [r[3] for r in weak] + local + other_safety
            changed = True

    if dry:
        print(f"\n{C}(dry-run){RST} not written.")
        return 0
    if not changed:
        print(f"\n{G}✓ chain already capability-ordered.{RST}")
        return 0

    bak = CFG.with_name(f"config.yaml.bak.rank-{time.strftime('%Y%m%d-%H%M%S')}")
    bak.write_text(CFG.read_text(encoding="utf-8"), encoding="utf-8")
    cfg["fallback_providers"] = new_fb
    CFG.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")
    print(f"\n{G}✓ chain reordered by capability{RST} ({len(new_fb)} fallbacks). backup: {bak.name}")
    if not do_eval:
        print(f"{DIM}Tip: `--eval` verifies with live test calls; `--set-primary` promotes the top model.{RST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
