#!/usr/bin/env python3
"""
Chain health check + safe live-first reordering.

This is the *safe* version of the "dynamically rate models, drop dead ones,
use the next when one runs out" idea. Unlike the old auto_improve background
thread, it:
  - only ever touches models ALREADY in your config (never invents/scrapes IDs),
  - never removes the local/OAuth safety net,
  - never empties the chain on a transient network blip,
  - runs on demand (setup, or `./bin/hermes --health`), not as a hidden daemon.

It pings each fallback provider's /chat/completions (max_tokens=1), then rewrites
fallback_providers as: live (fastest first) → throttled → [codex, ollama safety net].
Dead entries (bad key / missing model) are dropped. If the primary model is dead
and a live fallback exists, the fastest live model is promoted to primary.

Usage:
    python3 scripts/health_check.py                # ping + reorder + write
    python3 scripts/health_check.py --dry-run      # ping + report only
    python3 scripts/health_check.py --timeout 15
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Run via the Hermes venv python.")

REPO = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CFG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"

G, Y, R, C, DIM, RST = "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[2m", "\033[0m"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def ping(base_url: str, key: str, model: str, timeout: float, attempts: int = 3) -> tuple[str, float]:
    """Return (status, latency_seconds). status in live|throttled|dead.

    HTTP responses are deterministic and classified immediately. Only transient
    failures (timeout / connection reset) are retried, so a network blip can't
    false-drop a working provider.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode()
    last_dt = 0.0
    for i in range(attempts):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(1)
                return ("live", time.time() - t0)
        except urllib.error.HTTPError as e:
            dt = time.time() - t0
            if e.code == 429:
                return ("throttled", dt)          # rate-limited = alive, keep
            if e.code in (400, 404, 422):
                return ("dead", dt)               # model genuinely unavailable
            if e.code in (401, 403):
                return ("dead", dt)               # bad/rejected key
            return ("throttled", dt)              # 5xx — provider hiccup, keep
        except Exception:
            last_dt = time.time() - t0            # transient — retry
            time.sleep(0.6 * (i + 1))
    return ("dead", last_dt)


def main() -> int:
    dry = "--dry-run" in sys.argv
    timeout = 12.0
    if "--timeout" in sys.argv:
        timeout = float(sys.argv[sys.argv.index("--timeout") + 1])

    if not CFG_PATH.exists():
        sys.exit(f"no config at {CFG_PATH} — run bootstrap first")

    cfg = yaml.safe_load(CFG_PATH.read_text()) or {}
    catalog = yaml.safe_load((REPO / "providers.yaml").read_text())
    pcat = catalog.get("providers", {})
    env = load_env()

    # custom:<name> base_url/key_env can also come from config.custom_providers
    custom = {f"custom:{c['name']}": c for c in (cfg.get("custom_providers") or [])}

    def resolve(provider: str):
        """Return (kind, base_url, key). kind in native|custom|oauth|local.

        oauth/local are the safety net — classified from the catalog FIRST so a
        local model (ollama) is never pinged/dropped just because it isn't running
        on this machine.
        """
        meta = pcat.get(provider, {})
        kind = meta.get("kind", "native")
        if kind == "oauth":
            return ("oauth", None, None)
        if kind == "local":
            c = custom.get(provider, {})
            return ("local", c.get("base_url") or meta.get("base_url"), "")
        if provider in custom:
            c = custom[provider]
            return ("custom", c.get("base_url"), env.get(c.get("key_env", ""), ""))
        return (kind, meta.get("base_url"), env.get(meta.get("key_env", ""), ""))

    fallbacks = cfg.get("fallback_providers") or []
    print(f"\n{C}⚕ chain health check{RST}  ({CFG_PATH})\n")

    live, throttled, safety, dead_list = [], [], [], []
    for entry in fallbacks:
        prov = entry.get("provider", "")
        model = entry.get("model", "")
        kind, base, key = resolve(prov)

        if kind in ("oauth", "local"):
            safety.append(entry)
            print(f"  {DIM}• keep{RST} {prov}/{model} {DIM}(safety net, not pinged){RST}")
            continue
        if not base or not key:
            print(f"  {Y}⚠ skip{RST} {prov}/{model} {DIM}(no base_url or key — dropping){RST}")
            continue

        status, dt = ping(base, key, model, timeout)
        if status == "live":
            live.append((dt, entry))
            print(f"  {G}✓ live{RST} {prov}/{model} {DIM}{dt:.2f}s{RST}")
        elif status == "throttled":
            throttled.append(entry)
            print(f"  {Y}~ throttled/hiccup{RST} {prov}/{model} {DIM}(keep, low priority){RST}")
        else:
            dead_list.append({"provider": prov, "model": model})
            print(f"  {R}✗ dead{RST} {prov}/{model} {DIM}(drop){RST}")

    # Advisory cache for the router (route.py) — avoid pointing at dead models.
    try:
        (HERMES_HOME / ".portable_health.json").write_text(
            json.dumps({
                "dead": dead_list,
                "throttled": [{"provider": e["provider"], "model": e["model"]} for e in throttled],
                "live": [{"provider": e["provider"], "model": e["model"], "latency": round(dt, 3)}
                         for dt, e in live],
            }))
    except Exception:
        pass

    # Preserve the curated quality order within each bucket (don't resort by raw
    # latency — a faster-but-weaker model shouldn't leapfrog a stronger one).
    # We only prune dead models and demote throttled ones below healthy ones.
    new_fallbacks = [e for _, e in live] + throttled + safety

    # Safety: never nuke the chain to just the safety net on a bad-network run.
    non_safety_new = len(live) + len(throttled)
    non_safety_old = len([e for e in fallbacks
                          if resolve(e.get("provider", ""))[0] not in ("oauth", "local")])
    if non_safety_new == 0 and non_safety_old > 0:
        print(f"\n{Y}⚠ every pinged provider failed — likely a network issue. "
              f"Leaving chain unchanged.{RST}")
        return 1

    # Promote fastest live model to primary if current primary is dead.
    primary = cfg.get("model", {}) or {}
    pp, pm = primary.get("provider", ""), primary.get("default", "")
    promoted = False
    if pp and pm and live:
        kind, base, key = resolve(pp)
        if kind not in ("oauth", "local") and base and key:
            st, _ = ping(base, key, pm, timeout)
            if st == "dead":
                top = live[0][1]  # highest-priority live model (curated order)
                print(f"\n{Y}⟳ primary {pp}/{pm} is dead → promoting "
                      f"{top['provider']}/{top['model']} to primary{RST}")
                cfg["model"] = {"provider": top["provider"], "default": top["model"]}
                # old primary drops out; promoted model leaves the fallback list
                new_fallbacks = [e for e in new_fallbacks if e is not top]
                promoted = True

    changed = (new_fallbacks != fallbacks) or promoted
    print()
    if not changed:
        print(f"{G}✓ chain already optimal — no changes.{RST}")
        return 0
    if dry:
        print(f"{C}(dry-run){RST} would reorder to:")
        for e in new_fallbacks:
            print(f"    {e['provider']}/{e['model']}")
        return 0

    cfg["fallback_providers"] = new_fallbacks
    CFG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=1000))
    print(f"{G}✓ updated chain ({len(live)} live, {len(throttled)} throttled, "
          f"{len(safety)} safety-net).{RST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
