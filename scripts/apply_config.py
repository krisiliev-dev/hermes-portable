#!/usr/bin/env python3
"""
Overlay config/config.template.yaml onto ~/.hermes/config.yaml.

Only the keys present in the template are written; every other key in an
existing config is preserved (so we don't fight Hermes's own schema/migration).
A timestamped backup is made whenever an existing config is modified.

Usage:
    python3 scripts/apply_config.py            # merge (keep other keys)
    python3 scripts/apply_config.py --force    # same, always overwrite our keys
"""
from __future__ import annotations

import os
import shutil
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

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Run via the Hermes venv python.")

REPO = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CFG = HERMES_HOME / "config.yaml"
TEMPLATE = REPO / "config" / "config.template.yaml"


def deep_merge(base: dict, overlay: dict) -> dict:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v          # lists (chain) & scalars replaced wholesale
    return base


def main() -> int:
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    overlay = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    existing = yaml.safe_load(CFG.read_text(encoding="utf-8")) if CFG.exists() else {}
    existing = existing or {}

    if CFG.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = CFG.with_name(f"config.yaml.bak.portable-{ts}")
        shutil.copy2(CFG, bak)
        print(f"  backed up existing config → {bak.name}")

    merged = deep_merge(existing, overlay)
    CFG.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")

    # sanity
    d = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    assert isinstance(d.get("custom_providers"), list), "custom_providers must be a list"
    assert d.get("fallback_providers"), "fallback_providers missing"
    print(f"  ✓ config written: model={d['model']['default']}, "
          f"{len(d['fallback_providers'])} fallbacks, "
          f"{len(d['custom_providers'])} custom providers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
