#!/usr/bin/env python3
"""
Task-aware model router (phase 2).

Classifies a prompt into a task class (coding / reasoning / fast / creative /
general) and picks the best model whose provider key you actually have. Prints
the chosen provider+model so a launcher can run:

    hermes -z "<prompt>" -m <model> --provider <provider>

Deterministic-first: a fast, transparent keyword classifier handles the obvious
cases with zero latency. Pass --explain to see why. Selection skips providers
whose key is missing and (if scripts/health_check wrote a cache) whose model is
known-dead, so routing never points at something that can't run.

Usage:
    route.py "<prompt>"                 # -> "<provider>\t<model>"
    route.py --explain "<prompt>"       # human-readable decision
    route.py --json "<prompt>"          # {"class","provider","model"}
    route.py --task coding "<prompt>"   # force a class
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Run via the Hermes venv python.")

REPO = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    envf = HERMES_HOME / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


# Tie-break: prefer specific "what" classes over the "how fast" class when
# scores are equal (e.g. "write a SHORT story" -> creative, not fast).
_TIE_PRIORITY = {"coding": 4, "reasoning": 3, "creative": 2, "fast": 1, "general": 0}


def classify(prompt: str, classes: dict) -> tuple[str, dict[str, int]]:
    """Return (best_class, scores). Keyword hits × class weight."""
    low = prompt.lower()
    scores: dict[str, int] = {}
    for name, spec in classes.items():
        hits = sum(1 for kw in (spec.get("keywords") or []) if kw.lower() in low)
        scores[name] = hits * int(spec.get("weight", 1))
    best = max(scores, key=lambda k: (scores[k], _TIE_PRIORITY.get(k, 0)))
    if scores[best] == 0:
        best = "general"
    return best, scores


def provider_key_ok(provider: str, providers_cat: dict, env: dict) -> bool:
    meta = providers_cat.get(provider, {})
    kind = meta.get("kind", "native")
    if kind == "local":
        return True                      # local always usable as a target
    if kind == "oauth":
        return True                      # codex via OAuth
    ke = meta.get("key_env")
    return bool(ke and env.get(ke))


def load_health_dead() -> set[tuple[str, str]]:
    """(provider, model) pairs known dead, if health_check wrote a cache."""
    cache = HERMES_HOME / ".portable_health.json"
    dead: set[tuple[str, str]] = set()
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            for e in data.get("dead", []):
                dead.add((e["provider"], e["model"]))
        except Exception:
            pass
    return dead


def select(task: str, profiles: dict, providers_cat: dict, env: dict):
    dead = load_health_dead()
    for cand in profiles["classes"].get(task, {}).get("prefer", []):
        p, m = cand["provider"], cand["model"]
        if (p, m) in dead:
            continue
        if provider_key_ok(p, providers_cat, env):
            return p, m
    return None, None


def main(argv: list[str]) -> int:
    explain = "--explain" in argv
    as_json = "--json" in argv
    forced = None
    if "--task" in argv:
        i = argv.index("--task")
        forced = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        sys.exit("usage: route.py [--explain|--json] [--task CLASS] \"<prompt>\"")
    prompt = " ".join(args)

    profiles = yaml.safe_load((REPO / "config" / "profiles.yaml").read_text())
    providers_cat = yaml.safe_load((REPO / "providers.yaml").read_text()).get("providers", {})
    env = load_env()

    if forced:
        task, scores = forced, {}
    else:
        task, scores = classify(prompt, profiles["classes"])
    provider, model = select(task, profiles, providers_cat, env)

    # Fall back to config primary if nothing in the profile is usable.
    fell_back = False
    if not provider:
        cfg = yaml.safe_load((HERMES_HOME / "config.yaml").read_text()) if (HERMES_HOME / "config.yaml").exists() else {}
        m = cfg.get("model", {}) or {}
        provider, model = m.get("provider"), m.get("default")
        fell_back = True

    if as_json:
        print(json.dumps({"class": task, "provider": provider, "model": model,
                          "fell_back": fell_back}))
    elif explain:
        top = sorted(scores.items(), key=lambda x: -x[1])[:3] if scores else []
        print(f"prompt : {prompt[:70]}{'…' if len(prompt) > 70 else ''}")
        print(f"class  : {task}" + (f"  (scores: {top})" if top else "  (forced)"))
        print(f"model  : {provider} / {model}" + ("  [config fallback]" if fell_back else ""))
    else:
        if not provider:
            return 1
        print(f"{provider}\t{model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
