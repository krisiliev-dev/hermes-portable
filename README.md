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
| 6 | cohere | command-r-plus | `COHERE_API_KEY` |
| 7 | openai-codex | gpt-5.5 | OAuth (quality safety-net, not free) |
| 8 | ollama | llama3.2-64k | local (offline last resort) |

You don't need all of them — **one key is enough** to start. Gemini is the
easiest free start: <https://aistudio.google.com/apikey>.

## Everyday commands

```bash
hermes                     # launch (uses the chain automatically)
./bin/hermes --setup       # re-run everything (config + keys + health)
./bin/hermes --keys        # add/update provider keys
./bin/hermes --health      # re-ping the chain and reorder live-first
hermes doctor              # validate the install
```

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

## Roadmap — per-task routing (phase 2)

"Use a coding model for coding" is not yet automatic here. Hermes natively biases
toward stronger models for coding via `openrouter.min_coding_score`. True
per-task routing (classify the request → pick the best chain member) belongs as a
small module hooked into Hermes's real inference path
(`agent/chat_completion_helpers.py`) — *not* the old interceptor that silently
no-op'd because the main agent has no `call_llm` method. Tracked as future work.

## License

MIT.
