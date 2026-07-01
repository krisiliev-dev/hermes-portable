"""hermes-portable — in-loop task router plugin.

Registers a ``pre_llm_call`` hook that classifies each turn's user message and
routes it to the best model for the task (reusing scripts/route.py). This makes
INTERACTIVE sessions route per message, not just one-shot `hermes ask`.

REQUIRES upstream support for a per-turn model override from ``pre_llm_call``
(return ``{"model","provider"}``) — see NousResearch/hermes-agent#56650. Until
that lands, the hook still loads harmlessly: it returns the override dict, and
an upstream without the feature simply ignores the extra keys (no error).

Enable (once the hook feature is in your Hermes):
    mkdir -p ~/.hermes/plugins
    ln -s "$PWD/plugin/hermes_portable_router.py" ~/.hermes/plugins/
    # then restart hermes / the gateway
Check with:  hermes plugins list
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def register(ctx) -> None:
    # route.py lives in scripts/ — import it without polluting global namespace.
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import route as _route  # noqa: E402
        import yaml  # noqa: E402
    except Exception as exc:  # pragma: no cover - defensive
        try:
            ctx.log(f"hermes_portable_router disabled: {exc}")
        except Exception:
            pass
        return

    profiles = yaml.safe_load((REPO / "config" / "profiles.yaml").read_text())
    providers_cat = yaml.safe_load(
        (REPO / "providers.yaml").read_text()
    ).get("providers", {})

    def _route_turn(user_message: str = "", model: str = "", **_):
        if not user_message:
            return None
        try:
            env = _route.load_env()
            task, _scores = _route.classify(user_message, profiles["classes"])
            provider, chosen = _route.select(task, profiles, providers_cat, env)
        except Exception:
            return None
        if provider and chosen and chosen != model:
            return {"model": chosen, "provider": provider}
        return None

    ctx.register_hook("pre_llm_call", _route_turn)
