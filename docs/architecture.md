# Architecture

## Core principle
The upstream GenericAgent repository is treated as **read-only**. This project does not patch or rewrite upstream files.

## Turn execution model
1. WeChat message arrives.
2. Parent process resolves the session conservatively: reuse the same user's existing session when possible; bind a new `context_token` back to that session when provided.
3. Parent process builds a prompt and launches a **one-turn helper subprocess**.
4. Helper subprocess imports GenericAgent from the configured upstream path.
5. Helper restores prior session state (`backend_history`, `history_info`, working memory).
6. Helper runs one GA turn and streams JSON progress events to the parent.
7. Parent renders progress / ask_user / final output back to WeChat.
8. Helper persists updated state and exits.

## Why this model
- no upstream repo modifications
- stable per-user session continuity even when `context_token` changes or disappears
- easy stop/reset semantics
- state survives worker restarts
- parent process can kill a stuck turn without corrupting the main server

## Session state
Stored under `state/sessions/<session-key>/`:
- `ga_state.json` — GA conversation state
- `logs/` — worker logs and GA model response logs
- `requests/` — per-turn prompt/image payloads sent to the worker
- `ipc/` — intervention files for steering/running-turn control

## Runtime workspace policy
- The upstream GA checkout stays unique and is **not** mirrored, copied, or symlinked into each session.
- Worker subprocesses run from the real `ga.root`.
- The actual GA working directory is the upstream checkout's shared `temp/`, matching original GenericAgent path assumptions such as `../memory` and `/new`-style resets.
- `ga-wechat-clawbot` still keeps per-session state/logs/request payloads under `storage.root`, but it does **not** attempt per-session isolated checkouts.

## Event protocol
Worker emits JSON lines on stdout:
- `progress`
- `ask_user`
- `done`
- `error`
- `aborted`

Stdout is reserved for these JSON events; all GA debug output is redirected to log files.
