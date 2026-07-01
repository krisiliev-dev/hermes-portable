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

## Deepen the free chain (many OpenRouter models)

OpenRouter hosts lots of free models. `discover_openrouter_free.py` queries their
**live** API, keeps the ones that are actually free + text-output + high-context
right now, and adds them to your chain so a rate-limit (429) on one rolls to the
next:

```bash
python3 scripts/discover_openrouter_free.py            # add to the chain
python3 scripts/discover_openrouter_free.py --dry-run  # just show what it'd add
```

(Windows PowerShell: `uv run --with pyyaml python scripts\discover_openrouter_free.py`,
with `$env:HERMES_HOME="$env:USERPROFILE\.hermes"` set.)

Bootstrap runs this automatically when an `OPENROUTER_API_KEY` is present. Re-run
anytime to refresh — the free list changes as OpenRouter adds/removes models.

## How failover actually works

The chain auto-falls-back on **capacity/transient errors** — HTTP 429
(rate-limit), 5xx, and timeouts — walking `fallback_providers` top to bottom. It
does **not** retry a **400 bad request** (resending a malformed request can't
succeed), so keep a *working* primary model (`hermes model`) — a failing primary
is the main way you end up seeing a hard error instead of a clean fallthrough.
Free tiers rate-limit aggressively, so a **deep** chain (many providers + many
free OpenRouter models) is what makes it feel seamless.

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
`{"model": ..., "provider": ...}` to override the turn's model. **That change is
proposed upstream in [NousResearch/hermes-agent#56650](https://github.com/NousResearch/hermes-agent/pull/56650).**

This repo already ships the companion plugin — `plugin/hermes_portable_router.py`
— which registers a `pre_llm_call` hook that classifies each turn (via
`scripts/route.py`) and returns the routed model. Once the upstream hook lands
(or you apply the patch), enable it:

```bash
mkdir -p ~/.hermes/plugins
ln -s "$PWD/plugin/hermes_portable_router.py" ~/.hermes/plugins/
hermes plugins list      # confirm it loaded, then restart hermes
```

Until then it loads harmlessly (a Hermes without the feature just ignores the
override), so interactive sessions keep using the health-ordered chain.

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

## Troubleshooting

**`hermes: not recognized` / `command not found` after bootstrap.** The install
succeeded but uv's tool-bin directory isn't on your PATH yet — common on Windows
when you bootstrap under Git Bash but launch under PowerShell (different PATHs).
Fix:

```bash
uv tool update-shell     # registers the bin dir on PATH; then reopen the terminal
```

If it's still not found, add the dir manually and reopen the terminal:

- **Windows (PowerShell):** uv installs to `%USERPROFILE%\.local\bin`
  ```powershell
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"   # current window
  [Environment]::SetEnvironmentVariable("Path","$env:USERPROFILE\.local\bin;" + [Environment]::GetEnvironmentVariable("Path","User"),"User")  # permanent
  ```
- **macOS/Linux:** it's `~/.local/bin` — add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc.

Always **open a fresh terminal** after a PATH change. Verify with `hermes --version`.

## License

MIT.
