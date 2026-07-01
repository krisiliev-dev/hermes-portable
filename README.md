# hermes-portable

Clone anywhere, run one command, get a working **free-first Hermes agent**.

```bash
git clone <your-fork>/hermes-portable
cd hermes-portable
./bootstrap.sh          # installs Hermes, applies the free chain, asks for keys, health-checks
hermes                  # start chatting
```

## What this is (and isn't)

This is a **thin overlay**, not a fork of Hermes. `bootstrap.sh` installs **clean
upstream [Hermes](https://github.com/NousResearch/hermes-agent)** (`hermes-agent`
from PyPI) and then layers on:

- a **free-first model chain** (`config/config.template.yaml`) — a frontier-class
  free model as primary, with fallbacks across *different* providers so one
  rate-limiting or dying rolls over to the next;
- **interactive key setup** (`scripts/setup_keys.py`) — detects which provider
  keys you already have and prompts for the missing free ones, with links to get them;
- a **safe health check** (`scripts/health_check.py`) — pings each model, drops
  dead ones, and orders the live ones fastest-first.

Because it installs stock Hermes, `hermes update` and every upstream feature keep
working. Nothing here edits Hermes's source.

## The default free chain

| # | Provider | Model | Key |
|---|----------|-------|-----|
| ★ | gemini | gemini-2.5-flash | `GEMINI_API_KEY` |
| 1 | zai | glm-4.7-flash | `GLM_API_KEY` |
| 2 | nvidia | nemotron-3-super-120b | `NVIDIA_API_KEY` |
| 3 | openrouter | gpt-oss-120b:free | `OPENROUTER_API_KEY` |
| 4 | alibaba | qwen3-max | `DASHSCOPE_API_KEY` |
| 5 | mistral | mistral-large-latest | `MISTRAL_API_KEY` |
| 6 | openai-codex | gpt-5.5 | OAuth (quality safety-net, not free) |
| 7 | ollama | llama3.2-64k | local (offline last resort) |

You don't need all of them — **one key is enough** to start. Gemini is the
easiest free start: <https://aistudio.google.com/apikey>.

## Everyday commands

```bash
hermes                     # interactive chat (uses the chain automatically)
./bin/hermes ask "..."     # TASK-ROUTED one-shot: picks the best model for the task
./bin/hermes route "..."   # preview which model routing would pick (no LLM call)
./bin/hermes --setup       # re-run everything (config + keys + health)
./bin/hermes --keys        # add/update provider keys
./bin/hermes --health      # re-ping the chain and reorder live-first
hermes doctor              # validate the install
```

## Task-aware routing (`ask`)

`hermes ask "<prompt>"` classifies the prompt and runs the **best model for that
kind of work** — a strong open coder for code, a flash model for quick questions —
via `hermes -z "<prompt>" -m MODEL --provider PROVIDER`. The full fallback chain
still applies underneath if the picked model fails.

```
$ hermes route "debug this python traceback"
class  : coding
model  : nvidia / nvidia/nemotron-3-super-120b-a12b

$ hermes route "give me a quick tldr"
class  : fast
model  : gemini / gemini-2.5-flash
```

- Task classes and their model preferences live in `config/profiles.yaml`
  (`coding`, `reasoning`, `fast`, `creative`, `general`). Edit freely.
- Classification is a fast, transparent keyword scorer (zero added latency);
  `hermes route --explain "..."` shows the scores. Force a class with
  `hermes ask --task coding "..."`.
- Routing only picks providers whose key you have, and skips models the last
  health check marked dead.
- **Scope:** routing applies to one-shot `ask`. Interactive sessions are **not**
  auto-routed per message — see below. For interactive use, the health-ordered
  chain handles quality + failover, and you can switch manually with `/model`.

### Why interactive isn't auto-routed (and the clean fix)

Investigated against upstream Hermes 0.18. Its plugin hooks can inject context
(`pre_llm_call`) or skip/rewrite a message (`pre_gateway_dispatch`), but **none
can override the model for a turn** — `pre_llm_call` receives `user_message` and
`model` but no agent handle, and its return only appends context. So per-message
routing can't be done from a plugin today. Patching the agent loop directly would
work but `hermes update` would clobber it — the exact fragile pattern this repo
avoids.

The clean path is a tiny upstream change: let a `pre_llm_call` callback return
`{"model": ..., "provider": ...}` to override the turn's model (it already has
`user_message` in hand), or add a `pre_model_select` hook that receives the agent
so a plugin can call `switch_model()`. With that, `scripts/route.py` drops in as
a plugin and interactive sessions route per message too.

## Adding a newly-discovered free provider

1. Add an entry under `providers:` in `providers.yaml` (name, `key_env`,
   `base_url`, `free_url`, `health_model`).
2. Add it to `fallback_providers:` in `config/config.template.yaml` where you
   want it in priority order.
3. `./bin/hermes --setup` — it prompts for the new key and health-checks it.

That is the whole "find more free agents, ask for the key, add it" loop — done
deliberately by you (or a reviewed PR), not by an unattended daemon.

## Design notes / safety

- **No self-rewriting daemon.** An earlier experiment ran a background thread
  that rewrote `config.yaml` from unvalidated data and web-scraped model IDs; it
  clobbered the chain. Chain changes here happen only via `health_check.py`,
  which *only reorders models already in your config* and never drops the
  local/OAuth safety net or empties the chain on a network blip.
- **Config overlay is non-destructive.** `apply_config.py` merges only the keys
  it owns and backs up any existing `config.yaml` first.
- **Keys** live in `~/.hermes/.env` (chmod 600), never in this repo.

## License

MIT.
