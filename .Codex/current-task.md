# Current task

## Deep Entity Knowledge Graph & Android Companion Transparent Memory Hub DELIVERED (2026-10-01)

- **Trụ cột 1: Deep Entity Knowledge Graph Store & Schema (`memory/models.py`, `memory/graph.py`, `memory/sqlite.py`)**:
  - Triển khai `EntityNode` (`entity_nodes`) và `EntityRelation` (`entity_relations`) với composite unique constraint trên `(source_id, relation, target_id)`.
  - Triển khai `EntityGraphStore`: thread-safe SQLite operations, 1-hop subgraph query, cascade entity deletions, thống kê (`stats`), và `purge`.
  - Khởi tạo bảng idempotent `init_graph_tables()`.
  - Verified: `tests/test_entity_graph.py` (4/4 passed).
- **Trụ cột 2: Sensitive Data Sanitizer & Privacy Boundary (`memory/sanitizer.py`)**:
  - `SensitiveDataSanitizer`: thuật toán Luhn cho thẻ tín dụng, regex cho Google/OpenAI/GitHub/Bearer API keys, và regex nhận diện mật khẩu/PIN/OTP (`mật khẩu là: ...`, `password: ...`).
  - Cung cấp `is_sensitive()`, `redact()`, và `validate_for_storage()`. Chặn đứng nguy cơ lưu trữ secrets/credentials vào bộ nhớ.
  - Verified: `tests/test_sensitive_sanitizer.py` (4/4 passed).
- **Trụ cột 3: First-Class Memory Tools & Prompt Guidance (`tools/builtins/memory_tools.py`, `prompts/system.md`)**:
  - `RememberFactTool` (`remember_fact`, capability `memory.remember`, `ToolRisk.SAFE`, `SideEffect.IDEMPOTENT`) tự động tiền kiểm qua sanitizer.
  - `ForgetFactTool` (`forget_fact`, capability `memory.forget`, `ToolRisk.SAFE`, `SideEffect.IDEMPOTENT`).
  - Đăng ký capability trong `core/capabilities/factory.py`, đăng ký vào `_builtin_tools` trong `tools/factory.py`, gắn allowlist trong `server/runtime.py`.
  - System prompt bổ sung Mục 5 (`Personal Memory & Knowledge Management`) hướng dẫn Aura chủ động gọi công cụ khi người dùng cung cấp thông tin hoặc yêu cầu quên.
  - Verified: `tests/test_memory_tools.py` (4/4 passed), `tests/test_device_boundary.py` (14/14 passed).
- **Trụ cột 4: Hybrid Reflection & 1-Hop Graph Context Injection (`memory/reflection.py`, `memory/knowledge.py`, `server/runtime.py`)**:
  - `EpisodicReflectionWorker`: background extractor trích xuất bộ ba thực thể `(source, relation, target)` qua rules đa ngôn ngữ (Vi/En) và LLM fallback, tự động loại bỏ lượt chat nhạy cảm.
  - `MemoryKnowledgeProvider`: mở rộng truy vấn 1-hop subgraph quan hệ thực thể theo từ khóa câu hỏi của người dùng và định dạng vào Prompt (`knowledge - <source> <relation> <target>`).
  - Verified: `tests/test_memory_knowledge_graph.py` (2/2 passed).
- **Trụ cột 5: Authenticated REST Memory API (`server/routes/memory.py`, `server/main.py`)**:
  - Các endpoints: `GET /api/memory/overview`, `GET /facts`, `POST /facts`, `DELETE /facts/{key}`, `GET /graph`, `POST /entities`, `DELETE /entities/{name}`, `POST /relations`, `DELETE /relations`, `GET /episodes`, `DELETE /episodes/{id}`, `POST /purge`.
  - Verified: `tests/test_memory_api.py` (3/3 passed).
- **Trụ cột 6: Android Companion Multi-Tier Memory Hub (`android/`)**:
  - Wire DTOs `MemoryDtos.kt` & Retrofit contract `AuraApi.kt`.
  - Repository wrapper methods `AuraRepository.kt`.
  - `MemoryHubViewModel.kt` quản lý state độc lập, tìm kiếm thời gian thực theo từ khóa, lọc category, thêm/xóa fact, thêm/xóa thực thể, thêm/xóa quan hệ, xóa sự kiện, và tẩy sạch toàn bộ.
  - Giao diện `MemorySection.kt` hiện đại với 4 tab: *Hồ sơ sự thật (Facts)*, *Mạng thực thể (Entity Graph)*, *Dòng thời gian (Episodic)*, và *Cấu hình & Tẩy sạch (Settings & Purge Danger Zone)*.
  - Verified: `MemoryHubViewModelTest.kt` (6/6 passed). Toàn bộ Android Gradle suite (`:app:testDebugUnitTest`): BUILD SUCCESSFUL (22/22 tasks up-to-date, 0 failures).
- **Trụ cột 7: Full System Verification & Regression**:
  - Python tests mới: 31/31 passed 100% trong 1.88s.
  - Core regression: 126/126 passed 100% trong 10.46s.
  - Device boundary invariant: 14/14 passed.
  - Android test suite: 457 unit tests passed.

---

## Settings API Coroutine Warning Fix & Desktop `open_url` Tool DELIVERED (2026-10-01)

- **Trụ cột 1: Settings API Async Restart Clean Coroutine Handling (`server/routes/settings.py`)**:
  - Resolved `AURA-TASK-001` un-awaited coroutine warning on `_do_restart` in `update_settings` and `reset_settings`.
  - Hoisted imports (`asyncio`, `os`, `sys`) to module level and bypassed physical `os.execve` during test runs (`PYTEST_CURRENT_TEST`).
  - Added coroutine directly to FastAPI `background_tasks.add_task(_do_restart)` ensuring clean async loop execution without un-awaited Task warnings.
  - Verified: `tests/test_settings_api.py` (71 passed, zero warnings with `-W error::RuntimeWarning`), `tests/test_settings_contract.py` (123 passed).
- **Trụ cột 2: Phase 8 Desktop `open_url` Tool (`tools/builtins/desktop.py`, `core/capabilities/factory.py`)**:
  - Implemented `OpenUrlTool` (`open_url`, capability: `desktop.open_url`, `ToolRisk.SAFE`, `SideEffect.IDEMPOTENT`).
  - Strict security validation: enforces `http` or `https` schemes only (rejecting `file://`, `javascript:`, `data:`, etc.) and requires non-empty host domain.
  - Registered capability in `core/capabilities/factory.py` with discovery metadata.
  - Registered in `tools/factory.py` under `_pc_tools(config)` when `"open_url" in (config.get("allowed") or [])`, strictly maintaining the stock server cloud boundary invariant (`tests/test_device_boundary.py`).
  - Added to allowed desktop tools in `server/runtime.py` and documented in `prompts/system.md`.
- **Trụ cột 3: Testing & Verification**:
  - Dedicated unit tests: `tests/test_open_url_tool.py` (9/9 passed).
  - Stock boundary invariant: `tests/test_device_boundary.py` (14/14 passed).
  - Regression: `tests/test_sandbox_tools.py` + `tests/test_stream_tool_calling.py` + `tests/test_pc_tools.py` (96 passed, 1 skipped).

---

## Sandbox Execution, Autonomous Tool Synthesis, Timeout Anti-Hang Hardening, & Speculative Streaming DELIVERED (2026-10-01)

- **Trụ cột 1: Built-in Sandbox Execution & Custom Tool Creation (`tools/builtins/sandbox.py`)**:
  - `ExecuteSandboxPythonTool` (`python_sandbox`, capability: `sandbox.execute`, `ToolRisk.SAFE`): Safely executes arbitrary Python 3 code in an isolated subprocess with scrubbed environment and captured exit codes/evidence.
  - `SynthesizeCustomTool` (`create_custom_tool`, capability: `tools.synthesize`, `ToolRisk.SAFE`): Dynamic synthesis, validation, sandbox test execution, auto-promotion into `ToolRegistry`, dynamic authorization, and SQLite provenance tracking.
  - Capability integration in `core/capabilities/factory.py` (`sandbox.execute`, `tools.synthesize`) and registry integration in `tools/factory.py`.
- **Trụ cột 2: Timeout Anti-Hang Hardening & Subprocess Process Tree Termination (`tools/sandbox/runner.py`, `tools/builder/synthesis.py`)**:
  - `SandboxRunner`: Increased `default_timeout` from 10.0s to 20.0s to accommodate Windows process launch latency.
  - Implemented `_terminate_process` using `taskkill /F /T /PID` on Windows to cleanly purge the entire subprocess tree, avoiding pipe-locking hangs in `proc.communicate()`.
  - Tuned `ToolSynthesisEngine`: Set `max_attempts = 2` to avoid cumulative synthesis retries exceeding client network timeouts.
- **Trụ cột 3: Tool Awareness & Prompt Awakening (`brain/prompt_builder.py`, `prompts/system.md`)**:
  - Enriched system instructions (`prompts/system.md`) with explicit guidance to actively call `python_sandbox` for calculations, algorithms, and logic verification instead of computing mentally, and `create_custom_tool` when asked to create or teach new capabilities.
  - Added structured `TOOL AWARENESS & CAPABILITIES` section under `TOOLS` in `PromptBuilder`.
- **Trụ cột 4: Speculative Buffering for Streaming Tool Calls (`brain/conversation.py`, `server/runtime.py`)**:
  - Enabled tools in streaming chat (`chat_stream`) via `can_offer_tools` and `offer_tools: bool = True` in `server/runtime.py`.
  - Implemented speculative buffer for stream start (up to 40 characters): detects if reply begins with `{"tool":`. If detected as tool call, silently consumes stream, resolves tools, and streams grounded answer without leaking raw JSON to UI. If normal conversation, immediately flushes buffer with zero perceptible latency.
- **Trụ cột 5: Testing & Verification**:
  - New test suites: `tests/test_sandbox_tools.py` (7/7 passed), `tests/test_stream_tool_calling.py` (5/5 passed).
  - Full regression test suite: 332/332 tests passed across tools, pc tools, tool calling, hardware probe, context compaction, and dynamic synthesis.

---

## First-Boot Hardware Probe, Conversational Context Compaction, & Companion Loading Bar DELIVERED (2026-10-01)

- **Trụ cột 1: First-Boot Host Hardware & Environment Scan (`core/hardware_probe.py`)**:
  - `HostEnvironment`: comprehensive scan of hostname, manufacturer, model, OS release/build, CPU name/threads/cores, RAM total/available, GPU model(s), storage free/total, machine UUID, active network adapters, username.
  - Windows CIM/PowerShell deep inspection with cross-platform fallback.
  - Hardcoded persistence into SQLite `ProfileStore` (`category="system"`). Runs automatically on first startup, or on demand via `--rescan-hardware` / `/rescan-hardware` / `rescan_system_hardware` tool.
  - Rich CLI banner and prompt injection section (`HOST ENVIRONMENT`) in `brain/prompt_builder.py` and `brain/prompt_sections.py`.
- **Trụ cột 2: Conversational Context Compaction (`brain/compaction.py`, `brain/conversation.py`)**:
  - `ConversationCompactor`: condenses older conversational turns beyond threshold into an AI-synthesized/rule-based synopsis (`[COMPACTED CONTEXT]`) while preserving recent $N=6$ turns verbatim.
  - Reduces token usage, API latency, and reasoning confusion on long chats.
  - Manual trigger via `/compact` CLI and chat command, plus automatic background compaction in `ConversationManager._prepare()`.
- **Trụ cột 3: Android Companion Initial Loading Bar**:
  - Added `isInitialScanning` and `scanStatusText` to `ChatUiState.kt`.
  - Added `LinearProgressIndicator` in `ChatScreen.kt` indicating system connection and device inspection state.
  - Wired lifecycle in `ChatViewModel.kt` to display during initial probe and dismiss upon health completion.
- **Trụ cột 4: Testing & Verification**:
  - Python tests: `tests/test_hardware_probe.py` (5/5 passed), `tests/test_conversation_compaction.py` (6/6 passed).
  - Regression: `tests/test_tools.py` + `tests/test_memory_v2.py` (114/114 passed).

---

- **Trụ cột 1: Android Companion Native Task Tools & Permissions Hub**:
  - Implemented `DeviceTaskDispatcher.kt` with `DeviceTaskToolCatalog`, `DeviceTaskHandler`, `AndroidDeviceTaskHandler`, and `DeviceTaskDispatcher`.
  - Added native directive routing in `DeviceToolDispatcher.kt` and capabilities (`android.sms`, `android.calendar`, `android.contacts`).
  - Added permissions in `AndroidManifest.xml` (`SEND_SMS`, `READ_SMS`, `READ_CALENDAR`, `WRITE_CALENDAR`, `READ_CONTACTS`).
  - Hub UI (`ToolsSection.kt`, `DevicePermissions.kt`) shows real-time status of device permissions.
  - Tested: `DeviceTaskDispatcherTest.kt` (5/5 unit tests passed). Android Gradle suite: 451/451 tests passed.
- **Trụ cột 2: Proactive Intelligence 24/7 Outbox & Stream Dispatch**:
  - `AuraDaemon` in `daemon/supervisor.py` feeds evaluated proactive recommendations to `NotificationOutbox` (`PendingNotification`).
  - Thread-safe step pruning with `db_lock` and startup tick synchronization.
  - Runtime integration in `server/runtime.py`.
  - Tested: `tests/test_phase1_runtime_closure.py` (10/10 passed), `tests/test_proactive.py` (126/126 passed).
- **Trụ cột 3: Hybrid Semantic Memory Retrieval**:
  - `HybridConversationRetriever` in `memory/retrieval.py` using Reciprocal Rank Fusion ($k=60.0$) combining lexical token matching and dense embedding similarity (`GeminiEmbeddingProvider`).
  - Failsafe degradation to lexical retrieval when embedding provider is unavailable.
  - Tested: `tests/test_hybrid_retrieval.py` (8/8 passed). Semantic memory suite: 57/57 passed.
- **Trụ cột 4: Documentation & State Sync**:
  - Updated `docs/ROADMAP.md`, `docs/IMPLEMENTATION_STATUS.md`, `.Codex/progress.md`, `.Codex/current-task.md`, `.Codex/project-state.md`.

---

## Phase 5 Personal Task Tools & Android Companion Verification Badges DELIVERED (2026-10-01)

- Grounded personal task tools in Android Companion bridge:
  - `SendSMS` (`android.send_sms`), `ReadSMS` (`android.read_sms`).
  - `CreateCalendarEvent` (`android.create_calendar_event`), `ListCalendarEvents` (`android.list_calendar_events`).
  - `SearchContacts` (`android.search_contacts`).
- Registered capabilities in `core/capabilities/factory.py` (`android.sms`, `android.calendar`, `android.contacts`) and wired into `server/routes/agent.py`.
- Android Companion UI updates:
  - 1-tap connection presets for `Local (127.0.0.1:8000)` and `Render Cloud (https://aura-xwm4.onrender.com/)`.
  - Streaming verifier metadata parsing in `AuraStreamClient.kt` & `ChatViewModel.kt`.
  - `✓ Verified` badge rendered on verified turns in `ChatComponents.kt`.
- Verified test suites:
  - `tests/test_android_task_tools.py`: 13/13 passed.
  - Focused android suite: 63/63 passed.
  - Full pytest suite: 3,842 passed.
  - Android Gradle unit tests: `:app:testDebugUnitTest --rerun-tasks` BUILD SUCCESSFUL (22/22 executed, 0 failures).

---

## Gemini Semantic Embeddings & Cloud-Only Memory Consolidation DELIVERED (2026-10-01)

- Native `GeminiEmbeddingProvider` implemented in `memory/embeddings.py` (`google.genai.Client.models.embed_content`, model: `text-embedding-004`).
- Strict privacy consent boundary (`memory.semantic.allow_remote`) enforced.
- Excised legacy `OllamaEmbeddingProvider`, deprecated `"ollama"` provider in factory, cleaned docstrings across `core/config.py`, `config.yaml`, and `vision/processor.py`.
- Tested and verified: 49/49 tests in `test_semantic_memory.py` passed (100%), 233/233 focused capability & provider tests passed.
- Next steps: Remote git push upon user request.

---

## Cloud-Only Architecture Migration (Phases 0–6) DELIVERED (2026-09-23)

Complete migration of AURA from hybrid local/on-device learning to pure Cloud-Only architecture:
- Python backend decoupled and 54 legacy local files removed (`learning/`, `brain/providers/{local,local_aura,ollama}.py`, etc.).
- Android companion app purged of on-device LLM engines and native llama binaries; direct cloud chat operational.
- Settings store deprecation mappings active (`local_aura` / `on_device` / `local` / `ollama` -> `gemini`).
- Error reporting mapped: `ProviderAuthError` (502), `ProviderTimeoutError` (504), `ProviderUnavailableError` (503).
- **Phase 6 Disk Cleanup Completed**: ~54.2 GB `brains/` directory, `data/aura/` datasets, dead training scripts and reports purged with high caution. All active components (`agent/autonomy_guard.py`, `KaomojiAvatar.kt`, `confirmations.py`, `companion_sqlite.py`, etc.) preserved intact.
- **All tests verified**: 496/496 Python tests passed; Android `./gradlew.bat :app:testDebugUnitTest` BUILD SUCCESSFUL.

---

## Phase 5B.3 — Autonomous Capability Gap → Self-Extension Runtime Wiring DELIVERED (2026-09-14)

Deliverable: Real user request autonomous self-extension pipeline in `AgentRuntime`, verified on live Windows laptop with Gemini 3.5 Flash Lite provider.

### Key Deliverables Implemented & Verified:
1. **Autonomous Synthesis Policy (`tools/builder/policy.py`)**:
   - `AutonomousSynthesisPolicy` governing runtime platform eligibility (`local`, `python`, `win32`, `linux`), risk levels (`SAFE`), and recursion depth (`max_depth=1`).
   - Lexical security token filter blocking hazardous requests (`subprocess`, `rm`, `delete`, `kill`, `shell`, `exec`, `eval`, `token`, `password`, `key`, `sms`, `network`, `http`, etc.).
   - Concurrency deduplication: `acquire_synthesis_lock` / `release_synthesis_lock` prevents redundant concurrent syntheses for the same capability.
2. **AgentRuntime Inline Wiring (`agent/runtime.py`)**:
   - Integrated `_maybe_synthesize_gap(run)` at Round 0 in `advance(run)` before `_model_round()`.
   - On gap detection (`CapabilityGapState.SYNTHESIZABLE`), triggers autonomous synthesis, AST validation, sandbox testing, approval, promotion, and dynamic authorization.
   - Refreshes `_tools_payload` in-place so newly synthesized tool schema is immediately passed to model for native function calling in the **exact same run/turn**.
3. **Production API & Startup Rehydration Integration**:
   - `server/routes/agent.py`: Wired `ToolSynthesisEngine` (with `BrainRouter`) and `AutonomousSynthesisPolicy` into `get_intent_runtime()`.
   - `tools/factory.py:build_registry()`: Automatically invokes `rehydrate_active_tools(registry)` on startup.
   - `tools/builder/builder.py` & `rehydrate.py`: Dual-registered tool capability identifiers into `core.capabilities.registry` with keyword metadata.
4. **Testing & Live Verification**:
   - `tests/test_phase5b3_agent_synthesis.py`: **22/22 PASSED (100%)**
   - Full Phase 5B Regression Suite: **83/83 PASSED (100%)** (`test_phase5b3_agent_synthesis.py`, `test_phase5b_synthesis.py`, `test_phase5b_dynamic_integration.py`, `test_phase5b_runtime.py`).
   - Live Laptop E2E Verification (`scripts/verify_phase5b3_live.py`): **All 8 live scenarios PASSED (100%)** using real Gemini 3.5 Flash Lite model on Windows laptop host.

---

## Phase 5B.2 — Autonomous Tool Synthesis & Capability Gap Wiring DELIVERED (2026-09-14)

Deliverable: Full autonomous tool self-extension lifecycle from intent to grounded response, verified on live Windows laptop with Gemini 3.5 Flash Lite provider.

### Key Deliverables Implemented & Verified:
1. **Autonomous Tool Synthesis Engine (`tools/builder/synthesis.py`)**:
   - `ToolSynthesisRequest`, `ToolSynthesisResult`, `ToolSynthesisEngine`
   - `FORBIDDEN_SYNTHESIS_TOKENS` lexical defense against recursive self-modification
   - Iterative refinement retry loop feeding validation error feedback back into prompt
   - Strict architectural contract adherence: `Tool` subclassing, `ToolRisk`, `SideEffect`, `parameters`, `ToolResult` with `Evidence`
2. **Security & Sandbox Hardening (`tools/builder/builder.py`, `tools/builder/validator.py`)**:
   - AST validation rejecting unauthorized imports (`subprocess`, `os`, `socket`), banned calls (`os.system`, `eval`), and reflection
   - Subprocess sandbox isolation for candidate test execution
   - Operator approval gate enforcement in `ToolBuilder.promote()`
3. **Capability Gap Integration (`server/routes/capabilities.py`)**:
   - `POST /api/capabilities/synthesize` endpoint connecting `CapabilityGapEngine` to `ToolSynthesisEngine`
   - Capability auto-registration in `core.capabilities.registry` on promotion
4. **Persistence & Lifecycle**:
   - SQLite `ToolProvenanceRecord` persistence and SHA-256 digest validation on startup rehydration
   - Disabling/revocation lifecycle enforcement in `ToolExecutor`
5. **Testing & Live Verification**:
   - `tests/test_phase5b_synthesis.py`: **16/16 PASSED**
   - Regression suites (`test_phase5b_dynamic_integration.py`, `test_phase5b_runtime.py`, `test_capabilities.py`, `test_tools.py`, `test_tool_calling.py`): **245/245 PASSED (100%)**
   - Live Laptop E2E Verification (`scripts/verify_phase5b2_live.py`): **All 10 live scenarios PASSED** using live Gemini 3.5 Flash Lite model

---

# Phase 5A (previous task, closed)

Phase 5A **LIVE-CLOSED** — 2026-09-05. Every item of the Phase 5A.8 brief is
VERIFIED on real hardware (`IBCQMB4PTGNZJVTO`), including the final mutating-
action link: one live `android.launch_app` on the stock calculator produced an
observed `postcondition {"verified": true}` that reached
`ClaimState.VERIFIED` with `VerifierDecision.PASS` and an unmodified reply.
Chain proof 16/16; evidence and the exact device report in
`.Codex/progress.md` (2026-09-05 final).

All Phase 1–5A work is COMMITTED and PUSHED on `origin/feature/aura-identity`.

## Status table

| Item | State |
| --- | --- |
| Repo integrity (nothing discarded) | VERIFIED |
| Phase 1–5A committed + pushed | VERIFIED |
| Installed APK == current source | VERIFIED (sha256 match) |
| Companion connection + token | VERIFIED (heartbeat 200; token never read) |
| 15 Android capabilities AVAILABLE | VERIFIED |
| `android.app_inventory` AVAILABLE live | VERIFIED |
| Live `PackageManager` enumeration | VERIFIED (277 pkgs, 3.80 s) |
| Inventory freshness / no cache | VERIFIED (`observed_at` 23.9 s old) |
| Diagnostics privacy | VERIFIED (0 leaks in 10,567 lines) |
| postcondition → POSTCONDITION Evidence | VERIFIED |
| Evidence → `ClaimState.VERIFIED` | VERIFIED (live, decision PASS) |
| **Verified postcondition on a mutating action** | **VERIFIED (live, 16/16)** |
| False postcondition → CONTRADICTED | VERIFIED (read-only + mutating tools) |
| Bare `{"ok": true}` is not verification | VERIFIED (INFERRED, not VERIFIED) |
| Observation ≠ postcondition | VERIFIED |
| Regression vs baseline | VERIFIED — 3489/2/1/5, zero regressions |

Nothing in Phase 5A remains UNKNOWN or BLOCKED.

## Open items

1. **Connection URL is `http://127.0.0.1:8000/`** — left deliberately for the
   owner to restore to `https://aura-xwm4.onrender.com/` through the
   Connection UI, which pre-fills the token from state
   (`ui/hub/ConnectionSection.kt`) and therefore preserves it. Verified
   earlier that the Render URL works: the companion long-polled it
   successfully once Render had woken.
2. **Production is 18 commits behind.** `origin/main` has neither
   `tools/outcome.py` nor `brain/verify/`, so the deployed server cannot emit
   POSTCONDITION Evidence or grade a ClaimState. Everything verified here
   lives on `feature/aura-identity` only. Merging is a separate decision.
3. **Not fixed on purpose** (verify-only): `ToolResult.capability` is the
   literal `"unknown"` for bridge reports, so claim binding rests on tool name
   and outcome text; and repair phrasing can pick an awkward object noun
   ("I can't verify that the android was actually confirmed").
4. `tests/conftest_caps.py` and `tests/conftest_capabilities.py` are dead,
   unreferenced scratch. Left on disk, deliberately NOT committed and NOT
   deleted — the owner's call.
5. The stock calculator is left in the foreground on the device; pressing home
   would have been a second unrequested mutation.

## NEXT

Phase 5 task tools (calendar / email / SMS / contacts). Still gated on the
security review for dangerous permissions, and on API level: AppFunctions is
Android 16+ and this device is API 33.

---

## Historical record below (superseded, kept deliberately)

The 2026-08-31 offline foundation and the 2026-09-01 stopped live attempt are
retained as written, because their statuses were honest at the time. One of
them was wrong and is corrected here: the 2026-09-01 entry recorded the
installed APK as the OLD pre-Phase-5A build, but the install did land
(`lastUpdateTime=2026-09-01 08:31:25`, sha256 match with the local build).

## Phase 5A offline foundation — 2026-08-31

Scope at the time: offline/CI-verifiable only; NO live device work, NO
APK/install, NO companion, NO URL/token restore. See
`.aura/decisions/ADR-010.md`.

What was implemented (all reusing existing primitives, no new model, no new
dependency, no cache):
- Capability `android.app_inventory` (registered canonically in
  `core/capabilities/factory.py`, `required_dependencies=["android.companion"]`,
  same accessibility health/permission gates as the other 14 Android caps).
  Registration alone never claims availability.
- Tool `android.list_apps` (`AndroidListApps`, `tools/providers/android_provider.py`):
  `ToolRisk.SAFE`, `SideEffect.READ_ONLY`, structured output, `observed_at`
  required, `device_id` when known.
- Bridge validation `normalise_device_report` + `_valid_inventory`
  (`android_bridge.py`): malformed inventory → `EXECUTION_FAILED`, never
  success, never coerced; UNKNOWN stays UNKNOWN.
- Android half `android/.../accessibility/AppInventory.kt` (`PackageSource` /
  `PlatformPackageSource` / `AppInventory`): pure, deterministic, JVM-testable;
  launchability from the MAIN/LAUNCHER query only; `QUERY_ALL_PACKAGES` not added.
- Evidence seam: `_evidence_from_report` / `tool_result_from_report`
  (`android_provider.py`) convert a device postcondition with an explicit
  boolean into canonical Phase 3 `EvidenceKind.POSTCONDITION`; an
  `app_inventory` observation becomes `EvidenceKind.OBSERVATION` (never
  memory). Closes the chat-path gap (verified Android action could not reach
  VERIFIED). `verified=true` → VERIFIED; `verified=false`/missing/malformed →
  never VERIFIED; bare `{ok:true}` is never verification.
- Dispatcher wiring `DeviceToolDispatcher.kt`: `android.list_apps` dispatch +
  `ACCESSIBILITY_CAPABILITIES` (includes `android.app_inventory`).

Offline verification:
- Python: `tests/test_android_inventory.py` 36 passed; full suite
  `3489 passed / 2 skipped / 1 deselected / 5 failed` — the 5 are exactly the
  pre-existing settings-restart set; baseline 3453 → +36, zero regressions.
- Android JVM: `AppInventoryTest.kt` 26 passed (0 failures) after fixing a
  compile error (`app()` helper must accept a nullable package name) and a
  false manifest assertion (the manifest only *mentions* QUERY_ALL_PACKAGES in
  a comment; it is not declared). Full JVM run: 414 tests / 2 failures — the 2
  are pre-existing `SettingsContractTest` fixture-drift failures (working-tree
  `providers.json` / `provider_health.json` were updated to a live configured
  Gemini by earlier uncommitted baseline work; unrelated to Phase 5A).
- `git diff --check` clean for Phase 5A files.

Capability states:
- Offline verifier/pipeline inventory: VERIFIED (via tests).
- Live `PackageManager` enumeration: IMPLEMENTED BUT NOT VERIFIED (device
  disconnected).
- Performance of real enumeration: UNKNOWN (no handset measurement).

NEXT (deferred, live-device): re-connect `IBCQMB4PTGNZJVTO`, install the APK,
run real `android.list_apps`, and verify the postcondition→VERIFIED path over a
real heartbeat. Restore stored URL to `https://aura-xwm4.onrender.com/` via the
Connection UI (keeps the token). Then Phase 5 task tools (calendar/email/SMS/
contacts) — still blocked on device + security review.

## Phase 5A.8 live verification attempt (2026-09-01) — STOPPED: DEVICE DISCONNECTED

Steps completed before the stop (all read-only, no device mutation):
- Repo state re-checked: branch feature/aura-identity, Phase 1–5A uncommitted
  work present, nothing reset/checked-out/cleaned.
- Device WAS connected (API 33, com.aura.companion v0.1.0 installed,
  accessibility services enabled + active, app process alive).
- Current Phase 5A debug APK built successfully (JDK 17, documented Gradle
  wrapper); provenance = fresh build of this working tree. NOT installed —
  Step 4 was never reached, so the INSTALLED v0.1.0 APK is still the OLD build
  and provably does not contain Phase 5A.
- Server started via documented entrypoint (config.yaml); authenticated
  /api/device/poll responded 401 without auth / 200 with auth (host-side).
- Reverse tunnel adb reverse tcp:8000 tcp:8000 set; transport device→host
  PROVEN at TCP level (device-originated connection reached a host listener).

BLOCKER (stop condition hit): the device disconnected mid-Step-5 and
`adb wait-for-device` blocks indefinitely — physical reconnect required.

What that leaves UNVERIFIED (do not trust docs over this list):
- Installed APK freshness: the installed v0.1.0 is the OLD build.
- Companion connection settings could not be read (prefs read died with the
  device); whether the stored URL/token are usable is UNKNOWN.
- Live heartbeat, android.app_inventory AVAILABLE state, live
  PackageManager enumeration, diagnostics privacy on a live request,
  postcondition→Evidence on a live round trip, chat-path grounding live:
  all UNKNOWN / BLOCKED.
- Offline suites remain exactly as the baseline: 3489/2/1/5 — nothing about
  this session changed them, and nothing live was claimed.


# Current task

Phase 4 (claim→evidence response verifier) COMPLETE — 2026-08-30.
`brain/verify/` implements the deterministic boundary (claim states,
typed claims, request-scoped evidence ledger reusing Phase 3 Evidence,
hard action-claim rules, memory attribution, live-registry capability
checks, minimal repair, hallucination taxonomy, privacy-safe verifier
trace). Wired into `ConversationManager.chat` and `chat_stream` via the
launcher's `response.verify` config; `enabled:false` restores the
pre-Phase-4 pipeline. ADR: `.aura/decisions/ADR-009.md`.

Resumption fixes (2026-08-30): the sentence splitter's look-behind
collapsed whole replies into one claim (5 verifier test failures) —
replaced with a boundary regex plus an explicit false-end check
(abbreviations, initials); the first-person action pattern now tolerates
intervening adverbs ("I definitely already sent..."); the Android
settings fixture was regenerated for the new `response.verify` keys.
Full suite 3420 passed / 2 skipped / 1 deselected / 5 failed (the exact
pre-existing settings-restart set). Baseline 3357 / 5: +63, zero
regressions.

NEXT (unchanged):
AURA 2.0 contract. Phase 0 (audit) COMPLETE and human-approved. Phase 1
(provider capability registry + capability-first routing, per-request
diagnostic trace, stream reconciliation) COMPLETE and verified - see
`.Codex/progress.md` 2026-08-28 and the new
`tests/test_capability_routing.py` / `tests/test_diagnostics_trace.py`.

Phase 2 (hybrid semantic memory) COMPLETE and verified - 2026-08-29. See
`.Codex/progress.md` and `.aura/decisions/ADR-007.md`.

Phase 3 (structured tool output contract) COMPLETE - 2026-08-29. Device-
independent, per `.aura/decisions/ADR-008.md`. All requirements of the
Phase 3 STOP CONDITIONS were held: nothing was rebuilt that did not need to
be, and output schemas are only validated when a tool declares one (existing
string-returning tools are untouched). Full Python suite 3357 passed / 2
skipped / 1 deselected / 5 failed, where the 5 are exactly the pre-existing
settings-restart `no running event loop` set; baseline was 3311 / 5, so +46
passing, zero regressions. See `.Codex/progress.md` 2026-08-29 (Phase 3).

NEXT (in order, per the approved decisions):

1. Android task tools (Phase 5): interfaces, schemas, permission/policy
   scaffolding, mocks and tests ONLY - no real-device claims until the
   phone reconnects and end-to-end tests produce evidence. Still blocked:
   physical device `IBCQMB4PTGNZJVTO` disconnected; companion URL restore
   via Connection UI still outstanding.
2. Claim→evidence verifier (Phase 4, audit gap 6): loop-level verification
   exists; a response-level verifier over free-form chat does not.

Optional Phase 2 follow-up, only if a real corpus justifies it: benchmark
a model-backed provider and set its floor from the sweep. Worth doing
before semantic recall is turned on for real, since the shipped hashing
provider does not understand paraphrase.

---

AURA 2.0 Master Implementation Contract received (2026-08-28). Phase 0
(codebase audit against the contract) is COMPLETE: see
`AURA_ARCHITECTURE_AUDIT.md` at the repo root. WAITING ON HUMAN REVIEW of
that audit before Phase 1 (provider capability registry + router refactor)
begins — this is the contract's own Phase 0 gate. Three decisions are needed:

1. Approve gap ranking / phase order (audit sections 3 and 5).
2. Semantic memory: contract wants vector recall; the codebase documents a
   deliberate lexical-only decision. Override or keep?
3. Android task tools (SMS/email/contacts/calendar): contract wants them;
   device is API 33, AppFunctions is Android 16+, and dangerous permissions
   require security review. Scope decision needed.

Meanwhile the pre-existing device-verification task below stays blocked on
hardware.

---

Make AURA's Android capabilities runtime-grounded and executable through the invariant:

`intent -> discovery -> capability registry -> permission -> health/dependency -> ToolExecutor -> real Android -> ToolResult -> LLM`.

Current focus: BLOCKED on hardware. The strict Android capability integration
is committed and pushed (a97bc69 on feature/aura-identity). Everything that can
be verified without the phone has been verified.

BLOCKER: physical device `IBCQMB4PTGNZJVTO` is disconnected (`adb devices`
empty). The following remain NOT VERIFIED and require the phone:

- install the freshly built `app-debug.apk` (sha256 11f48b5675fe8c5b0...)
- runtime service check on the real device
- the 11 real capability executions through the AURA pipeline
- the real `NODE_NOT_FOUND` failure path
- observation and action grounding on live screen state
- final device state confirmation

RECOVERY TASK, still outstanding: the device's stored server URL is
`http://127.0.0.1:8000/` and must be restored to
`https://aura-xwm4.onrender.com/` through the app's Connection UI, which
preserves the stored token because that field is pre-filled from state
(`ui/hub/ConnectionSection.kt`). The `adb reverse` mapping is already gone -
it died with the USB disconnect - and the temporary local server has been
stopped. Do this the moment the device reconnects.

Current verified device state: the physical package is installed and both AURA
accessibility services are enabled/bound. The companion sent a live heartbeat
to an authenticated local server and all 14 canonical Android capabilities
were `AVAILABLE`.

Completed milestones in this task:

- Per-tool Android capabilities are dynamically registered and resolved from
  companion status.
- ToolExecutor and `/api/device/invoke` are the server execution gates.
- The HTTP harness preserves live capability evidence and structured failure
  codes.
- The legacy direct agent-step body is disabled; AgentRunDriver is the active
  companion agent path.
- Discovery ranking was verified for six Android intents; every intended
  capability ranked first, and `select_best_executable` returned none while
  the real device was unavailable.
- Full Python suite completed with 3228 passed, 2 skipped, 1 deselected, and
  5 pre-existing settings-restart failures. The focused capability/device
  suite completed with 545 passed. Android Gradle unit tests and compilation
  succeed with the documented JDK 21/TEMP/TMP workaround.
- Final local live API check confirms 14 Android capabilities are `AVAILABLE`,
  with `authorization=granted`, `health=healthy`, and no stale reason.
- Safe physical execution succeeded for foreground app, UI tree, UI search,
  screenshot, tap/back/home, launch, wait, verify, text input, and the fixed
  node-scoped backspace path. A real missing-node failure and unknown-tool
  rejection also returned structured results.
- The companion dispatcher now re-checks its own runtime capability status
  immediately before dispatching a known Android tool.
