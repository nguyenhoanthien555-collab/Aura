# Aura Task Queue

Derived from the full-project audit, ordered by the repair plan. It
superseded a pre-audit `task-queue.bak`, which was deleted in Phase 7
(AURA-P2-005) along with `current-task.bak`; both were generic templates
holding no state these files lack.

Status: `[x]` done and tested, `[ ]` outstanding, `[~]` decided but not
yet implemented.

## Phase 0-3 — done

- [x] Baseline suite established (885 passed)
- [x] AURA-P1-015 optional collaborators log instead of swallowing silently
- [x] AURA-P1-011 fallback errors keep their real exception type
- [x] AURA-P2-001 log level honours config and `AURA_LOG_LEVEL`
- [x] AURA-P1-012 provider chain logs what initialized and what was skipped
- [x] AURA-P0-006 agent ticks are not written into conversation memory
- [x] AURA-P0-007 machine turns are not styled; agent prompt drops STYLE
- [x] AURA-P0-008 lenient Android JSON parsing, retry instead of break
- [x] AURA-P1-003 `open_app` verification requires the target package
- [x] AURA-P1-004 intent routing - the phone can hold a conversation again
- [x] AURA-P0-001 bounded tool-calling loop in `ConversationManager`
- [x] AURA-P0-002 TOOLS / TOOL RESULTS prompt sections
- [x] AURA-P0-003 `OpenApplicationTool` reports real launch outcomes
- [x] AURA-P0-004 `applications` must be a mapping, and says so when it is not
- [x] AURA-P1-001 tools enabled with exactly one SAFE tool
- [x] AURA-P1-002 no server-side confirm handler - DANGEROUS unreachable there

## Phase 4 — honest remote/device capability boundary

- [x] AURA-P0-005(a) when Aura cannot reach the target machine, say so
      rather than describing the action. Must remain the default whenever
      no local agent is connected.
      Verified in Phase 7, both halves. Stated: the boundary is a SYSTEM
      instruction, present with an empty catalogue and with tools offered,
      and carried by the streaming path too. Structural: no device route
      exists, the shipped allowlist grants only a clock, and a
      hallucinated device tool is refused rather than run. Machine
      (agent-mode) turns are excluded, because those really can act.
      Pinned by `tests/test_device_boundary.py` in both languages the
      project is used in.

## Phase 5 — configuration and documentation consistency

- [x] AURA-P1-013 `memory.recall` is false while the docs present recall
      as working - enable it or document that it is off on purpose
      Done: documented as deliberate, not enabled. `config.yaml` now
      carries the rationale IMPLEMENTATION_STATUS had been quoting but
      which did not exist in the file, and both say outright that recall
      is lexical token overlap - no embedding model, no vector store.
- [x] AURA-P1-005 all sessions share one memory store (or document
      single-tenant and enforce it via auth)
      Done: documented single-tenant, enforcement already in place.
      `server/runtime.py:chat` now states that `session_id` scopes only
      the metadata endpoint and that the auth token is the identity
      boundary - mandatory at startup since AURA-P1-008. Pinned by
      `test_sessions_share_one_memory_store`, so partitioning cannot
      happen silently.
- [x] AURA-P1-006 `SessionManager.cleanup_old()` has no caller
      Was a real leak: zero callers repo-wide, and client-supplied ids
      create entries, so the dict only grew for the process lifetime.
      Fixed with a throttled sweep on both create paths - no thread, no
      scheduler - plus a shared *unlocked* `_expire`, because the lock is
      not reentrant. Safe because a Session is metadata only; history
      lives in `memory/`, so expiry discards no conversation. 7 tests.
- [x] AURA-P1-009 Ollama returns before the fallback chain is built
- [x] AURA-P1-010 Ollama host is not configurable and is absent on Render
- [x] AURA-P2-003 `cerebras.py` has no router branch - register or remove
      (kept, explicitly unwired; the file records what must be true first)
- [x] AURA-P2-004 `DEEPSEEK_API_KEY` is read by no code (documented as
      having no effect; no DeepSeek provider invented)
- [ ] AURA-P2-008 three different answers to "which local model"
      (reported in progress.md; `llm.ollama_model` now exists and holds
      the value the provider always used. The other qwen values configure
      the external coding agent, not Aura, and were left alone)
- [x] AURA-P2-009 Ollama model selected by a `startswith("gemini")` hack
- [x] AURA-P2-010 dead `fallback_provider` key reads as authoritative
      (was a LIVE bug: legacy-only config silently produced no failover)
- [x] Contradiction #1: `project-state.md` says Ollama, `config.yaml` says
      Gemini. RESOLVED, and it was a misreading rather than a conflict.
      `project-state.md:48` scopes Ollama to the external coding/debugging
      agent and says in the same line that it does NOT replace Aura's
      runtime LLM. `config.yaml:6` is Aura's runtime provider. Two
      different systems, correctly configured; nothing to align.
- [x] Groq/Mistral: populate the keys or remove all three touchpoints per
      provider together. Half-removal is worse than either.
      DECISION: KEEP both. Registered, model defaults, shipped chain
      membership and end-to-end failover tests (27c8dd4). Absent keys are
      not evidence of obsolescence.
- [x] Docs drift: `IMPLEMENTATION_STATUS.md`, `CLOUD_MIGRATION_AUDIT.md`
      describe capabilities that did not exist; test-count claims are
      unverified. Done in Phase 7 STEP 7. Both files said the suite had
      never been executed; `FOLDER_STRUCTURE.md` listed two files deleted
      this phase and omitted `server/` and `companion/` entirely. All
      counts now come from pytest and from the working tree.

## Phase 8 — local Windows device agent (Option B)

Renumbering note: this section was "Phase 6", then "Phase 7". It has been
pushed back twice by work pulled forward on instruction (security and
deployment hardening, then repository cleanup). Nothing in it has been
built, and it is the only substantial feature work still outstanding.

- [~] Transport: long-poll `/api/device/commands`, reusing the bearer
      auth in `server/auth.py`
- [~] The agent holds its own allowlist locally and refuses anything
      outside it, so a compromised server is not code execution on the PC
- [ ] `open_url` tool - does not exist yet, and is what "open YouTube on
      my PC" actually needs
- [ ] Agent registration/heartbeat, so the server knows whether Phase 4's
      honest refusal or a real dispatch applies

## Phase 7 — security, deployment, cleanup

Renumbering note: the security/deployment items below were pulled forward
and executed as "Phase 6 — Security & Deployment Hardening" ahead of the
Windows device agent, on instruction. The device-agent section above is
therefore still open and moves to Phase 7+. Only the five items marked
done were in that hardening scope; the cleanup items were explicitly out
of it.

- [x] AURA-P1-008 auth silently disabled when the token is empty, on a
      0.0.0.0 bind. Refuse to start unless `AURA_ALLOW_INSECURE=1`.
      Done: `enforce_auth_policy` in `server/config.py`, raised from the
      ASGI lifespan *outside* the `is_initialized` guard and from
      `launcher.py --server`.
- [x] AURA-P1-007 CORS wildcard with `allow_credentials=True`
      Done: `cors_policy` refuses credentials whenever a wildcard is
      present. The real exposure was preflight reflection, not GET.
- [x] AURA-P1-014 `/api/chat` returns an opaque 500 for every failure
      Done: `server/errors.py` classifies the existing typed provider
      errors into 429 / 503 / 500. No second error hierarchy.
- [x] docker-compose healthcheck passes even when the provider chain is
      dead. Done: new public `/api/ready` (readiness, 503 when not) and
      the healthcheck now targets it.
- [x] SQLite on an ephemeral Render filesystem - confirm the disk plan
      Done: investigated, not built. The free 1 GB `aura-data` disk at
      `/app/data` already documented in `docs/DEPLOYMENT.md` §2 is the
      fix; the consequence of omitting it is now written down.
- [x] AURA-P2-002 dead top-level `tts/`. Done: removed. Zero Python
      importers, no dynamic imports, no config/doc/test references; every
      file was 0 bytes except `providers/edge.py` (4 bytes of
      whitespace). `voice/tts/` is the live implementation.
- [x] AURA-P2-006 empty `D:\AURAserverroutes\` directory. Done: removed.
      Untracked and empty; a mis-quoted path that once became a literal
      directory name.
- [x] AURA-P2-007 ~2400 tracked Gradle/dex artifacts. Done: untracked
      with `git rm -r --cached` (nothing left the disk) and ignored.
      2400 -> 250 tracked files. `android/gradle/wrapper/` stays tracked:
      the wrapper jar is source and a checkout cannot build without it.
- [x] AURA-P3-001 lone Vietnamese comment in `core/logger.py`. Done.
      A Unicode-range sweep confirms it was the only one in Python source.
- [x] AURA-P3-003 no CI. Done: `.github/workflows/tests.yml` runs
      `pytest -q` on 3.11. It deliberately restates nothing from
      `pytest.ini`, so it cannot drift from it. No secrets referenced.
- [x] AURA-P3-004 move `tests/manual_*.py` to `scripts/`. Done: 6 files
      `git mv`d. None defined `test_`/`Test`/imported pytest, so pytest
      never collected them; collection count is unchanged.
- [x] AURA-P3-005 `vision.enabled: true` with `capture_screen: false`.
      Done: documented, not changed. The two flags mean different things -
      `enabled` is "is there screen awareness at all", `capture_screen` is
      "may pixels be read" - and the shipped pair gives window-title
      awareness without screenshots, which is the intended default. Both
      `config.yaml` and `launcher/services.py:_build_vision_processor`
      now say so. NEW FINDING while verifying this: `vision.model` has two
      consumers wanting different naming schemes. See progress.md.
- [x] AURA-P3-002 `pass` in an abstract method body. Done: one site,
      `brain/providers/base.py`, changed to `...` to match the repo
      convention. `memory/models.py` left alone - `pass` in a SQLAlchemy
      model body is the standard idiom, not this defect.
- [x] Contradiction #11: two Android accessibility XML configs. NOT A
      BUG - they configure two different services. One is a read-only
      screen observer (no `canPerformGestures` on purpose), the other is
      the acting agent (`canPerformGestures="true"`,
      `flagRetrieveInteractiveWindows`, 500ms timeout). The manifest
      references each from its own `<service>`.

## Needs hardware, not code

- [ ] `"mở youtube"` end to end on a real Android device
- [ ] Desktop run with a live provider, to confirm the model actually
      emits the documented tool-call shape
