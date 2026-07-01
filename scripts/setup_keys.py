#!/usr/bin/env python3
"""
Interactive API-key setup for hermes-portable.

Reads providers.yaml, checks which keys already exist in ~/.hermes/.env, and
interactively prompts for the missing FREE providers (showing where to get a
key). Everything is optional/skippable — press Enter to skip. As long as one
LLM provider has a key, Hermes will run.

Usage:
    python3 scripts/setup_keys.py            # interactive
    python3 scripts/setup_keys.py --check    # report only, no prompts (exit 1 if no LLM key)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Run this via the Hermes venv python, or: pip install pyyaml")

REPO = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
ENV_PATH = HERMES_HOME / ".env"

GREEN, YELLOW, CYAN, DIM, RESET = "\033[32m", "\033[33m", "\033[36m", "\033[2m", "\033[0m"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def write_env(updates: dict[str, str]) -> None:
    """Merge updates into ~/.hermes/.env, preserving existing lines & order."""
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


def main() -> int:
    check_only = "--check" in sys.argv
    catalog = yaml.safe_load((REPO / "providers.yaml").read_text())
    providers = catalog.get("providers", {})
    extras = catalog.get("extras", {})
    env = load_env()
    updates: dict[str, str] = {}

    # Default the local Ollama key so the offline fallback "just works".
    if "OLLAMA_LOCAL_KEY" not in env:
        updates["OLLAMA_LOCAL_KEY"] = "ollama"

    def has_key(name: str) -> bool:
        p = providers[name]
        ke = p.get("key_env")
        if p.get("kind") == "oauth" or ke is None:
            return False  # handled elsewhere (OAuth via `hermes model`)
        if p.get("kind") == "local":
            return True   # local, no remote key needed
        return bool(env.get(ke) or updates.get(ke))

    configured = [n for n in providers if has_key(n)]
    llm_keys = [n for n in providers
                if providers[n].get("kind") not in ("oauth", "local")
                and (env.get(providers[n].get("key_env") or "") )]

    print(f"\n{CYAN}⚕ hermes-portable — provider keys{RESET}")
    print(f"  .env: {ENV_PATH}")
    print(f"  configured LLM providers: {GREEN}{len(llm_keys)}{RESET} "
          f"({', '.join(llm_keys) if llm_keys else 'none'})\n")

    if check_only:
        write_env(updates) if updates else None
        if not llm_keys:
            print(f"{YELLOW}⚠ No LLM provider key found. Run without --check to add one.{RESET}")
            return 1
        return 0

    print("Enter a key to enable a provider, or press Enter to skip.\n")
    for name, p in providers.items():
        if p.get("kind") in ("oauth", "local"):
            continue
        ke = p["key_env"]
        if env.get(ke) or updates.get(ke):
            print(f"  {GREEN}✓{RESET} {name} ({ke}) already set")
            continue
        tag = f"{GREEN}[free]{RESET}" if p.get("free") else f"{YELLOW}[paid]{RESET}"
        print(f"\n  {CYAN}{name}{RESET} {tag}")
        if p.get("note"):
            print(f"    {DIM}{p['note']}{RESET}")
        if p.get("free_url"):
            print(f"    get a key: {p['free_url']}")
        try:
            val = input(f"    {ke}= ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  (stopping key entry)")
            break
        if val:
            updates[ke] = val

    # Optional extras (web search, github)
    print(f"\n{CYAN}Optional extras{RESET} (improve capability; Enter to skip)")
    for ke, meta in extras.items():
        if env.get(ke) or updates.get(ke):
            print(f"  {GREEN}✓{RESET} {ke} already set")
            continue
        print(f"\n  {ke}")
        if meta.get("note"):
            print(f"    {DIM}{meta['note']}{RESET}")
        if meta.get("free_url"):
            print(f"    get one: {meta['free_url']}")
        try:
            val = input(f"    {ke}= ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if val:
            updates[ke] = val

    if updates:
        write_env(updates)
        print(f"\n{GREEN}✓ wrote {len(updates)} value(s) to {ENV_PATH}{RESET}")
    else:
        print(f"\n{DIM}no changes{RESET}")

    env = load_env()
    llm_keys = [n for n in providers
                if providers[n].get("kind") not in ("oauth", "local")
                and env.get(providers[n].get("key_env") or "")]
    if not llm_keys:
        print(f"\n{YELLOW}⚠ No LLM provider key configured yet. Hermes needs at least one.{RESET}")
        print(f"  Tip: gemini is the easiest free start → {providers['gemini']['free_url']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
