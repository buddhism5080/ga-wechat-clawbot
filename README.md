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
- `/restart` / `/reboot` — safely restart the current bot process
- `/new` / `/reset` / `/clear` — immediately create a fresh clean session

You can also define custom command aliases in `config.toml`, including aliases that do **not** start with `/`.

```toml
[wechat.command_aliases]
"帮助" = "/help"
"状态" = "/status"
"停止" = "/stop"
"重启" = "/restart"
"新建" = "/new"
"模型" = "/llm"
```

Aliases match the first token exactly, so `帮助` can be a command alias while `帮助我分析一下` still goes through the normal chat path.

Progress and liveness tuning is configurable too:

```toml
[wechat]
# same-turn progress update throttle; set to 0 to disable throttling
progress_interval_sec = 12

# "still processing" keepalive interval
heartbeat_interval_sec = 60

# optional explicit restart command for /restart; leave blank to relaunch the
# current CLI command via a detached helper process
restart_command = "systemctl --user restart ga-wechat-clawbot.service"
```

The heartbeat defaults to **60 seconds**. Safe restart helper logs go to `state/logs/restart_helper.log`.

## Doctor
```bash
ga-wechat-clawbot --config config.toml doctor
```

## Important design choice
This project may **import** GenericAgent internals in child helper processes, but it never patches or rewrites upstream source files. Updating the upstream repo remains a normal pull/rebase workflow.
