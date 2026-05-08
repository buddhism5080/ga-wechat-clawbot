# GA WeChat Clawbot

Standalone WeChat Clawbot / iLink integration for **GenericAgent** that keeps the upstream GA repository untouched.

## Goals
- no edits to the upstream GenericAgent repo
- conservative session reuse per user; `context_token` is rebound as an alias when available
- clean markdown rendering for Clawbot
- text / image / voice / file / video send+receive
- strong session orchestration, stop/reset, and persisted state

## Architecture
- `ga_wechat_clawbot.wechat_client` — WeChat login, polling, media upload/download
- `ga_wechat_clawbot.rendering` — markdown cleanup, `ask_user` rendering, attachment summaries
- `ga_wechat_clawbot.session` — per-context orchestration, typing heartbeats, progress throttling
- `ga_wechat_clawbot.ga.turn_worker` — one-turn helper subprocess that imports GenericAgent from a configured upstream path, restores state, runs one turn, writes back state, and exits
- `ga_wechat_clawbot.ga_controller` — parent-side subprocess manager and JSON event reader

## Install
```bash
cd ga-wechat-clawbot
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[wechat]"
cp config.example.toml config.toml
```

## Run
```bash
ga-wechat-clawbot --config config.toml serve
```

## Chat commands
- `/help` / `/commands` — show command help
- `/status` / `/state` — show current session status and selected LLM
- `/llm` — list available models
- `/llm current` — show the current model
- `/llm N` or `/llm set N` — switch to model `N`
- `/stop` / `/abort` — stop the in-flight task
- `/new` / `/reset` / `/clear` — clear the current session and keep future messages on the same reused session path

## Doctor
```bash
ga-wechat-clawbot --config config.toml doctor
```

## Important design choice
This project may **import** GenericAgent internals in child helper processes, but it never patches or rewrites upstream source files. Updating the upstream repo remains a normal pull/rebase workflow.
