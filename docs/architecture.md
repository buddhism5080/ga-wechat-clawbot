# Architecture

## Core principle
The upstream GenericAgent repository is treated as **read-only**. This project does not patch or rewrite upstream files.

## Turn execution model
1. WeChat message arrives.
2. Session key is derived from `context_token`.
3. Parent process builds a prompt and launches a **one-turn helper subprocess**.
4. Helper subprocess imports GenericAgent from the configured upstream path.
5. Helper restores prior session state (`backend_history`, `history_info`, working memory).
6. Helper runs one GA turn and streams JSON progress events to the parent.
7. Parent renders progress / ask_user / final output back to WeChat.
8. Helper persists updated state and exits.

## Why this model
- no upstream repo modifications
- strong per-session isolation
- easy stop/reset semantics
- state survives worker restarts
- parent process can kill a stuck turn without corrupting the main server

## Session state
Stored under `state/sessions/<context-token>/`:
- `ga_state.json` — GA conversation state
- `work/` — generated files and per-session working directory
- `logs/` — worker logs and GA model response logs
- `requests/` — per-turn prompt/image payloads sent to the worker

## Event protocol
Worker emits JSON lines on stdout:
- `progress`
- `ask_user`
- `done`
- `error`
- `aborted`

Stdout is reserved for these JSON events; all GA debug output is redirected to log files.
