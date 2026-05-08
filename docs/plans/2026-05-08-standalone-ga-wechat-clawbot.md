# Standalone GA WeChat Clawbot Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a standalone WeChat Clawbot integration project that uses GenericAgent as an upstream dependency without modifying its repository, while supporting text/image/voice/file/video send+receive, session isolation, and strong rendering.

**Architecture:** The standalone project owns transport, rendering, and session orchestration. Each turn runs in an isolated helper subprocess that imports GenericAgent from a configured upstream path, restores session state, executes exactly one turn, streams progress as JSON events, persists state, and exits.

**Tech Stack:** Python 3.11+, stdlib, requests, pycryptodome, qrcode, pillow.

---

## Task 1: Inspect upstream GA black-box surfaces
**Objective:** Verify the safest non-invasive execution path.

**Files:**
- Read: `/tmp/GenericAgent/agentmain.py`
- Read: `/tmp/GenericAgent/ga.py`
- Read: `/tmp/GenericAgent/README.md`

**Verification:** Confirmed: upstream has a task mode and reusable internal modules, but images are not wired through `put_task()` in stock `agentmain.py`, so the standalone project will use its own helper subprocess that imports GA internals without patching the repo.

### Task 2: Scaffold standalone project
**Objective:** Create independent packaging, docs, and project metadata.

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `config.example.toml`
- Create: `src/ga_wechat_clawbot/__init__.py`

**Verification:** Project installs and package imports succeed without touching `/tmp/GenericAgent`.

### Task 3: Implement transport + rendering layers
**Objective:** Build WeChat client and human-friendly GA output rendering.

**Files:**
- Create: `src/ga_wechat_clawbot/types.py`
- Create: `src/ga_wechat_clawbot/wechat_client.py`
- Create: `src/ga_wechat_clawbot/rendering.py`

**Verification:** Unit tests cover ask_user formatting, markdown chunking, attachment extraction, and prompt construction.

### Task 4: Implement GA runner layer
**Objective:** Execute one GA turn in a child process, persist state, and stream progress.

**Files:**
- Create: `src/ga_wechat_clawbot/ga/worker_common.py`
- Create: `src/ga_wechat_clawbot/ga/turn_worker.py`
- Create: `src/ga_wechat_clawbot/ga/probe_worker.py`
- Create: `src/ga_wechat_clawbot/ga_controller.py`

**Verification:** Child process emits JSON events, supports llm listing/switching, and leaves upstream repo untouched.

### Task 5: Implement session/app/CLI layers
**Objective:** Add per-context session orchestration and runnable CLI.

**Files:**
- Create: `src/ga_wechat_clawbot/config.py`
- Create: `src/ga_wechat_clawbot/session.py`
- Create: `src/ga_wechat_clawbot/app.py`
- Create: `src/ga_wechat_clawbot/cli.py`

**Verification:** `/help`, `/status`, `/llm`, `/stop`, `/new` are supported by code path; sessions are keyed by `context_token`.

### Task 6: Add tests and run validation
**Objective:** Ensure rendering/config/controller behavior works in isolation.

**Files:**
- Create: `tests/test_rendering.py`
- Create: `tests/test_config.py`
- Create: `tests/test_ga_controller.py`
- Create: `tests/test_session.py`

**Verification:** `python3 -m unittest discover -s tests -v` passes and `python3 -m compileall src tests` succeeds.
