# Configuration Notes

## Minimal config
Copy `config.example.toml` to `config.toml` and set:
- `ga.root` — path to the upstream GenericAgent checkout you actually use
- `ga.python` — Python used to run worker subprocesses

## Upstream GA requirements
The upstream checkout should have either:
- `mykey.py`, or
- `mykey.json`

configured already, because worker subprocesses instantiate GenericAgent from that checkout.

## Optional voice transcoding
If you want outbound audio to send as native WeChat voice more often, configure:

```toml
[wechat]
voice_encoder_cmd = "pilk -i {input_q} -o {output_q}"
```

Available placeholders:
- `{input}` / `{output}` — raw paths
- `{input_q}` / `{output_q}` — shell-quoted paths (recommended, especially on Windows or when paths contain spaces)

If transcoding is not available or fails, the project falls back to sending the audio as a normal file.

## Optional command aliases
You can define extra command aliases under `wechat.command_aliases`.

```toml
[wechat.command_aliases]
"帮助" = "/help"
"状态" = "/status"
"停止" = "/stop"
"重启" = "/restart"
"新建" = "/new"
"模型" = "/llm"
```

Notes:
- Alias keys may omit the `/` prefix.
- Alias values should point at one of the built-in commands such as `/help`, `/status`, `/stop`, `/restart`, `/new`, or `/llm`.
- Matching is token-exact on the first token, so `帮助` can trigger help while `帮助我看看这个错误` is still treated as a normal user message.

## Progress throttling and processing heartbeat
Two timing knobs control how chatty the bot feels in WeChat:

```toml
[wechat]
# same-turn progress update throttle; set to 0 to disable throttling
progress_interval_sec = 12

# keepalive message interval while a turn is still running
heartbeat_interval_sec = 60
```

Notes:
- `progress_interval_sec` throttles repeated `progress` events from the same turn so WeChat does not get spammed.
- Set `progress_interval_sec = 0` to disable progress throttling entirely.
- `heartbeat_interval_sec` controls how often the bot sends `⏳ 还在处理中，请稍等...` when a turn stays quiet for too long.
- The default heartbeat interval is **60 seconds**.

## Process health heartbeat
The bot also writes a lightweight process health file to:

```text
Path(wechat.token_file).parent / "health.json"
```

For example, with the default token path, the health file will be `~/.wxbot/health.json`.

The JSON always includes:
- `status` — always `"healthy"` when the bot considers itself healthy
- `pid` — current bot process PID
- `ts` — Unix timestamp from `time.time()`
- `iso` — UTC wall-clock timestamp (`YYYY-MM-DDTHH:MM:SS`)

It may also include:
- `bot_id`
- `last_update_ts`
- `error`

The file intentionally excludes tokens, cookies, and other secrets.

## Safe restart command
The bot supports `/restart` / `/reboot` via a detached helper process so it does not kill its own live process before the replacement command is armed.

```toml
[wechat]
# optional explicit restart command; if blank, the helper relaunches
# `python -m ga_wechat_clawbot.cli --config <current-config> serve`
restart_command = "systemctl --user restart ga-wechat-clawbot.service"
```

Notes:
- For supervised deployments, prefer an explicit service-manager command here.
- If `restart_command` is blank, the bot falls back to relaunching its current CLI entrypoint.
- Helper logs are written to `state/logs/restart_helper.log`.
