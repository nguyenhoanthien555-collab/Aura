# AURA FORENSIC AUDIT
**Auditor**: Forensic Audit Agent (Strict Non-Implementation Mode)  
**Date**: 2026-10-02  
**Commit Inspected**: `d3269b1ecf85b61e7b09cf279ac724ef759ba005` (`feat(mcp,brain): implement MCP Gateway and ChatGPT Main Brain integration`)  
**Target Repository**: `d:\AURA` on branch `feature/aura-identity`  
**Reference Document**: `AURA_MASTER_PLAN_CHATGPT_MAIN_BRAIN.md`  

---

## 1. Executive Summary

This forensic audit investigates the true state of the codebase following the purported delivery of the *ChatGPT Main Brain & MCP Gateway* integration (`d3269b1`). The audit was conducted in adversarial mode: treating the previous agent's implementation report strictly as unsubstantiated claims and evaluating reality solely through static code analysis, git history, runtime execution paths, and live telemetry.

### Key Factual Findings:
1. **OpenAI API Client, Not ChatGPT Web/Desktop**: The implemented `ChatGPTProvider` (`brain/providers/chatgpt.py`) is an `OpenAICompatibleProvider` that connects to `https://api.openai.com/v1/chat/completions` using an `OPENAI_API_KEY`. It does **not** connect to ChatGPT Web, ChatGPT Desktop, or any authenticated consumer ChatGPT session.
2. **MCP Gateway Bypass of Response Verification**: When an external model (ChatGPT/Claude/Cursor) calls Aura via the newly created MCP Gateway (`POST /api/mcp` or `run_mcp_bridge.py`), Aura acts as an **MCP Tool Server**, returning tool outputs (`CallToolResult`). The final response text is generated directly on the external model's UI and **never enters Aura's `ResponseVerifier` or `EvidenceLedger`**. The claim that ChatGPT responses are verified against device evidence is **false in MCP mode**.
3. **Hardcoded Confirmation Bypass in Standalone MCP Bridge**: In `server/routes/mcp.py:execute_mcp_tool()`, the fallback execution path (which is always hit when running `scripts/run_mcp_bridge.py` standalone without a pre-existing `ServerRuntime`) instantiates a `ToolExecutor` with `auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS})`. This strips human confirmation from `DANGEROUS` and `SENSITIVE` tools across PC and Sandbox environments.
4. **Credential Leakage Vector via Query String**: `server/routes/mcp.py` allows token authentication via URL query parameter `?token=<TOKEN>` across all endpoints (`POST /api/mcp`, `GET /api/mcp/tools`, `GET /api/mcp/sse`, `POST /api/mcp/messages`). On Render Cloud, full query strings are logged in plaintext to access logs, reverse proxies, and CDN edge logs. Furthermore, if `settings.auth_token` is blank, auth fails open (`return "dev"`).
5. **Reasoning Model Incompatibility (`o1`/`o3-mini`)**: `ChatGPTProvider` inherits `OpenAICompatibleProvider`, which unconditionally passes `messages=[{"role": "system", ...}]` and `payload["temperature"]`. OpenAI reasoning models (`o1`, `o1-preview`, `o1-mini`) reject both `role: system` and `temperature`, triggering HTTP 400 Bad Request.
6. **Pre-Existing vs New Work Grounding**: `android.list_apps` was already fully implemented in Phase 5A (`AppInventory.kt`, `DeviceToolDispatcher.kt`, `android_bridge.py`, `android_provider.py`). It was not created in this milestone.
7. **Render Deployment Status**: The latest commit `d3269b1` has been pushed to GitHub `origin/feature/aura-identity`, but as of the audit timestamp, Render Cloud is still running the preceding revision. The MCP routes on `https://aura-xwm4.onrender.com/api/mcp` return HTTP 404. Live Cloud execution is **NOT VERIFIED LIVE**.

---

## 2. What Is Actually Implemented

| Subsystem / Feature | Audit Status | Code Evidence | Notes & Operational Limitations |
| :--- | :--- | :--- | :--- |
| **MCP JSON-RPC 2.0 Endpoint** | **IMPLEMENTED** | `server/routes/mcp.py:296` (`POST /api/mcp`) | Handles `initialize`, `ping`, `notifications/initialized`, `tools/list`, and `tools/call`. Tested in `tests/test_mcp_gateway.py`. |
| **MCP SSE Transport** | **PARTIALLY IMPLEMENTED** | `server/routes/mcp.py:338` (`GET /api/mcp/sse`), `:390` (`POST /api/mcp/messages`) | Implemented in FastAPI, but has in-memory session queues without TTL pruning; untested under long-lived socket disconnection or multi-worker Render setups. |
| **STDIO MCP Bridge Runner** | **IMPLEMENTED** | `scripts/run_mcp_bridge.py` | Verified working locally via stdin/stdout subprocess pipe. Relays in-process or via remote HTTP. |
| **ChatGPT Main Brain Provider** | **IMPLEMENTED (API ONLY)** | `brain/providers/chatgpt.py` | Connects to `api.openai.com/v1/chat/completions` with `OPENAI_API_KEY`. It is **NOT** a ChatGPT Web or ChatGPT Desktop session integration. |
| **Function Calling for ChatGPT Provider** | **IMPLEMENTED** | `brain/providers/openai_compatible.py:157` | Uses standard OpenAI tools payload schema. Handled by `native_fc.extract_turn`. |
| **Claim $\to$ Evidence Verification in MCP Mode** | **NOT IMPLEMENTED** | N/A | Mechanically impossible in current MCP architecture: the MCP server only receives `tools/call` and sends `ToolResult`. ChatGPT renders the final text on client-side, bypassing Aura's `ResponseVerifier`. |
| **Claim $\to$ Evidence Verification in `/api/chat` Mode** | **IMPLEMENTED** | `brain/conversation.py:982`, `brain/verify/verify.py` | Functions when Aura acts as the orchestrator calling OpenAI via `/api/chat`. `ToolResult.evidence` is recorded in `turn.ledger`. |
| **Android App Inventory (`android.list_apps`)** | **IMPLEMENTED (PRE-EXISTING)** | `android/.../AppInventory.kt`, `tools/providers/android_provider.py:425` | Was already present and tested in Phase 5A. Exposed through MCP via `get_device_registry()`. |
| **Render Cloud MCP Deployment** | **NOT VERIFIED LIVE** | Commit pushed to `origin/feature/aura-identity` | Live probe returns HTTP 404. Render has not finished container rollout. |

---

## 3. Actual Runtime Architecture

### Flow A: External ChatGPT / Claude / Cursor via MCP Gateway
```text
[User]
   │
   ▼
[External Brain: ChatGPT Desktop / Claude Desktop / Cursor]
   │
   │ (JSON-RPC 2.0 over STDIO or HTTP POST /api/mcp)
   ▼
[Aura MCP Gateway: server/routes/mcp.py]
   │
   ├── Auths via Header or ?token= (Query string leak risk)
   │
   ▼
[execute_mcp_tool()]
   │
   ├── If android.* ──► [get_device_executor(): ToolExecutor (auto_approve=ALL)]
   │                            │
   │                            ▼
   │                     [GatewayDeviceBridge ──► Long Poll ──► Oppo Android Companion]
   │
   └── If PC / Server Tool:
           ├── Try runtime.services.tools (ToolExecutor)
           │
           └── Fallback ──► [ToolExecutor (auto_approve={SAFE, SENSITIVE, DANGEROUS})] ⚠️ SECURITY BYPASS
   │
   ▼
[ToolResult (with Evidence in _aura_meta)]
   │
   │ (JSON-RPC Result returned to External Brain)
   ▼
[External Brain: ChatGPT Desktop / Claude Desktop / Cursor]
   │
   │ ⚠️ AURA HAS LOST CONTROL OF THE TURN
   │ ⚠️ ResponseVerifier IS NEVER CALLED ON THE FINAL TEXT
   ▼
[User sees unverified, hallucination-prone final answer on ChatGPT UI]
```

### Flow B: Aura `/api/chat` Orchestrator Calling ChatGPT Provider
```text
[User on Android Companion / Web UI]
   │
   ▼
[POST /api/chat ──► server/routes/chat.py]
   │
   ▼
[ConversationManager: brain/conversation.py]
   │
   ├── [ChatGPTProvider (brain/providers/chatgpt.py)]
   │         │
   │         ▼ (POST https://api.openai.com/v1/chat/completions)
   │     [OpenAI Cloud API]
   │         │
   │         ▼ (Returns tool_calls: [{"name": "android.launch_app", ...}])
   │
   ├── [ToolExecutor.execute()] ──► [Device / PC Execution]
   │         │
   │         ▼ (Returns ToolResult with Evidence)
   │
   ├── [_record_tool_evidence()] ──► [Appends ToolEvidence to turn.ledger]
   │
   ├── [OpenAI Cloud API generates proposed final reply]
   │
   ├── [ResponseVerifier.verify(reply, turn.ledger)] ──► ✅ AUDITED & ENFORCED
   │         │
   │         ├── If verified ──► PASS
   │         ├── If unverified ──► HEDGE (INFERRED)
   │         └── If failed ──► REPAIR (CONTRADICTED)
   │
   ▼
[Verified Final Response returned to User]
```

---

## 4. Critical Findings

### Finding 1: Fallback ToolPolicy Silently Auto-Approves DANGEROUS Tools
- **ID**: `SEC-MCP-001`
- **SEVERITY**: `HIGH`
- **FILE**: `server/routes/mcp.py`
- **CODE PATH**: `execute_mcp_tool()` lines 138–152
- **OBSERVED BEHAVIOR**:
  ```python
  registry = build_registry()
  executor = ToolExecutor(
      registry=registry,
      policy=ToolPolicy(
          enabled=True,
          allowed=frozenset(registry.names()),
          auto_approve=frozenset({
              ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
          }),
      ),
  )
  ```
  When `runtime` is not initialized (e.g. running `python scripts/run_mcp_bridge.py` standalone locally, or executing before `ServerRuntime` is ready), `execute_mcp_tool()` builds a standalone `ToolExecutor` that explicitly overrides the security default (`auto_approve={SAFE}`) to auto-approve `DANGEROUS` and `SENSITIVE` tools across all registered PC tools.
- **EXPECTED BEHAVIOR**:
  `ToolPolicy.auto_approve` must default to `{ToolRisk.SAFE}` unless explicitly configured otherwise by the user. SENSITIVE or DANGEROUS tools must fail closed or require explicit interactive confirmation.
- **WHY IT MATTERS**:
  Any MCP client connected to the bridge runner can execute destructive file operations, arbitrary Python code in sandbox, or system modifications without user confirmation.
- **MINIMAL FIX**:
  In `server/routes/mcp.py:execute_mcp_tool()`, use `policy=ToolPolicy.from_config(config)` or instantiate `ToolPolicy(enabled=True, allowed=frozenset(registry.names()), auto_approve=frozenset({ToolRisk.SAFE}))`.

---

### Finding 2: Token Exposure via Query Parameter (`?token=...`)
- **ID**: `SEC-AUTH-002`
- **SEVERITY**: `HIGH`
- **FILE**: `server/routes/mcp.py`
- **CODE PATH**: `_authenticate_mcp_request()` lines 41–70 and route signatures lines 299, 325, 341, 393
- **OBSERVED BEHAVIOR**:
  The bearer token can be passed as `?token=<TOKEN>` on all four endpoints (`/api/mcp`, `/api/mcp/tools`, `/api/mcp/sse`, `/api/mcp/messages`).
- **EXPECTED BEHAVIOR**:
  Query parameter authentication should be restricted strictly to `GET /api/mcp/sse` where browser `EventSource` cannot supply headers, and rejected on `POST /api/mcp`, `GET /api/mcp/tools`, and `POST /api/mcp/messages`.
- **WHY IT MATTERS**:
  Query parameters are recorded in plaintext in:
  - Render Cloud deployment and access logs.
  - Cloudflare edge logs and proxy caches.
  - Browser history and `Referer` headers.
  This allows anyone with access to monitoring, reverse proxy logs, or shoulder-surfing to capture the master `AURA_AUTH_TOKEN`.
- **MINIMAL FIX**:
  Restrict `token_query` extraction exclusively to the `GET /api/mcp/sse` endpoint. Enforce `Authorization: Bearer <token>` on all POST and REST endpoints. Use `secrets.compare_digest` for constant-time token comparison.

---

### Finding 3: Auth Fails Open When `settings.auth_token` Is Empty
- **ID**: `SEC-AUTH-003`
- **SEVERITY**: `HIGH`
- **FILE**: `server/routes/mcp.py`
- **CODE PATH**: `_authenticate_mcp_request()` lines 46–47
- **OBSERVED BEHAVIOR**:
  ```python
  if not settings.auth_token:
      return "dev"
  ```
  If `AURA_AUTH_TOKEN` is unset or blank in the server environment, `_authenticate_mcp_request` returns `"dev"` and permits arbitrary unauthenticated requests from the public internet.
- **EXPECTED BEHAVIOR**:
  On production cloud deployments, missing authentication tokens must fail closed (raise HTTP 500 or HTTP 401), preventing open access to tools.
- **WHY IT MATTERS**:
  A misconfigured cloud environment instantly exposes all PC, memory, and Android device tools to anyone who scans the `/api/mcp` endpoint.
- **MINIMAL FIX**:
  Check environment mode: if not explicitly local development, reject requests if `auth_token` is missing or raise startup error.

---

### Finding 4: Inability to Enforce `ResponseVerifier` on External MCP Brain Responses
- **ID**: `ARCH-VERIF-004`
- **SEVERITY**: `HIGH`
- **FILE**: `server/routes/mcp.py` & `AURA_MASTER_PLAN_CHATGPT_MAIN_BRAIN.md`
- **CODE PATH**: MCP Tool Execution Life-cycle
- **OBSERVED BEHAVIOR**:
  The Master Plan states:
  > *"Aura decides whether an action is allowed. Aura executes. The world provides observations. Evidence determines what Aura is allowed to claim."*
  In MCP Gateway mode, Aura only acts as the tool provider. When ChatGPT calls `android.launch_app`, Aura returns `{"content": [...], "_aura_meta": {...}}`. The conversational response ("I opened YouTube for you") is rendered entirely by OpenAI/ChatGPT client-side. Aura's `ResponseVerifier` is completely bypassed.
- **EXPECTED BEHAVIOR**:
  To enforce truthful claims when ChatGPT is the Main Brain, Aura must either:
  1. Act as the conversational proxy/gateway (wrapping ChatGPT completion calls through `ConversationManager._verify_text()`), OR
  2. Embed strict verification directives and evidence summaries directly inside the tool's text content output so the model prompt is explicitly grounded.
- **WHY IT MATTERS**:
  ChatGPT can still hallucinate tool success or claim actions that were never verified by the device, completely violating Core Invariant 12 of the Master Plan.
- **MINIMAL FIX**:
  Ensure tool result text formatted in `_format_mcp_result()` includes explicit, unambiguous machine-readable evidence banners (e.g. `[EVIDENCE: VERIFIED postcondition confirmed by device]` or `[EVIDENCE: FAILED: Device is offline - DO NOT CLAIM SUCCESS]`), and document that client-side rendering cannot be intercepted by an MCP server.

---

### Finding 5: `ChatGPTProvider` Incompatible with Reasoning Models (`o1`, `o3-mini`)
- **ID**: `LLM-COMPAT-005`
- **SEVERITY**: `MEDIUM`
- **FILE**: `brain/providers/chatgpt.py` & `brain/providers/openai_compatible.py`
- **CODE PATH**: `_payload()` line 68 & line 83; `generate_with_tools()` line 182 & line 230
- **OBSERVED BEHAVIOR**:
  When `ChatGPTProvider` formats payloads, it puts `system` instruction as `{"role": "system", ...}` and includes `"temperature": ...`.
  OpenAI's `o1`, `o1-preview`, and `o1-mini` models reject `role: "system"` (they require `developer` or `user`) and reject custom `temperature` parameters.
- **EXPECTED BEHAVIOR**:
  If a user configures `llm.chatgpt_model: "o1"`, the provider should map `system` to `developer` (or merge into user prompt) and omit `temperature`.
- **WHY IT MATTERS**:
  Configuring `chatgpt_model` to an `o1` reasoning model causes runtime crashes with HTTP 400 Bad Request.
- **MINIMAL FIX**:
  In `_payload()` and `generate_with_tools()`, inspect `self.model`: if `self.model.startswith(("o1", "o3"))`, map system message to `role: "developer"` and drop `temperature`.

---

### Finding 6: Unbounded In-Memory SSE Session Queues
- **ID**: `RES-LEAK-006`
- **SEVERITY**: `LOW`
- **FILE**: `server/routes/mcp.py`
- **CODE PATH**: Lines 36–38, 349–354, 401–404
- **OBSERVED BEHAVIOR**:
  `_sse_sessions: dict[str, asyncio.Queue] = {}` stores queues mapped by UUID. Although `_sse_sessions.pop(session_id, None)` is called in the `finally:` block of `mcp_sse_transport`, if a client creates a session ID or establishes an aborted connection before the stream generator starts, or if `request.is_disconnected()` is delayed, uncollected queues can linger.
- **EXPECTED BEHAVIOR**:
  A bounded session cache with timestamped eviction / TTL.
- **WHY IT MATTERS**:
  Low-impact memory growth over long periods of continuous operation on 512MB Render containers.
- **MINIMAL FIX**:
  Add a last-active timestamp and prune sessions older than 30 minutes during heartbeat ticks.

---

## 5. MCP Audit

### Protocol Compliance (MCP 2024-11-05 Specification):
- **`initialize`**: Fully compliant. Returns `protocolVersion: "2024-11-05"`, `capabilities: {"tools": {"listChanged": false}}`, and `serverInfo: {"name": "Aura Cloud MCP Gateway", "version": "1.0.0"}`.
- **`ping` & `notifications/initialized`**: Fully compliant. Handled without errors and returns `{}`.
- **`tools/list`**: Fully compliant. Invokes `tools/schema.py:mcp_export()` to return `name`, `description`, and `inputSchema` (`type: "object"`, `properties`, `required`).
- **`tools/call`**:
  - Parameters: Extracts `name` and `arguments`.
  - Result: Returns standard `content: [{"type": "text", "text": ...}]` and `isError: bool`.
  - Error Handling: Missing `name` returns JSON-RPC `-32602`. Unknown methods return `-32601`. Tool execution failures return `isError: True` with the structured error instead of HTTP 500, allowing the caller to reason about errors.
- **Request IDs**: Preserves request ID (`id`) across all responses. Batch requests (`isinstance(body, list)`) are handled.

### Architectural Gap:
MCP by design is an **asymmetric tool-provider protocol**. An MCP Server cannot compel an MCP Client to utter verified truths. Aura can only guarantee that the data *inside* `tools/call` result is truthful.

---

## 6. ChatGPT / OpenAI Audit

### API vs Consumer Web/Desktop Reality:
- **What was implemented**:
  `brain/providers/chatgpt.py` is an **OpenAI REST API client**. It requires an `OPENAI_API_KEY` and calls `https://api.openai.com/v1/chat/completions`.
- **What it is NOT**:
  It is **not** a ChatGPT Web automation tool, browser cookie session, or reverse-engineered ChatGPT Desktop client.
- **Naming Disambiguation**:
  Calling this provider `chatgpt` is a branding convenience. In technical terms, it is an **OpenAI Chat Completions provider** pinned to `gpt-4o`.
- **Model Support**:
  - `gpt-4o`: Fully supported (chat, streaming, function calling).
  - `gpt-4o-mini`: Fully supported.
  - `o1` / `o3-mini`: **BROKEN / UNSUPPORTED** due to `role: system` and `temperature` rejections in `OpenAICompatibleProvider`.

---

## 7. Evidence & Verification Audit

### Verification Chain Forensic Trace:
```text
Task: "Open YouTube on Android"

[Android Device: Oppo CPH2251]
   │ (Executes launch intent, checks foreground package)
   ▼
[ToolResultReport(ok=true, postcondition={action: "com.google.android.youtube", verified: true})]
   │
   ▼
[tools/providers/android_provider.py:_evidence_from_report()]
   │
   ▼
[Evidence(kind=EvidenceKind.POSTCONDITION, source="android.postcondition", verified=True)]
   │
   ▼
[ToolResult.evidence is populated]
```

### Path 1: Inside `/api/chat` (Aura as Brain Orchestrator)
1. `ConversationManager._record_tool_evidence()` extracts `result.evidence` and adds it to `turn.ledger`.
2. Model generates response: *"I opened YouTube."*
3. `ResponseVerifier.verify(reply, turn.ledger)` matches `android.postcondition` `verified=True`.
4. Claim marked `ClaimState.VERIFIED`.
5. **Verdict**: ✅ **WORKING & GROUNDED**.

### Path 2: Inside `/api/mcp` (External Brain calling Aura)
1. External client calls `POST /api/mcp` (`tools/call`).
2. `execute_mcp_tool()` returns `ToolResult`.
3. `_format_mcp_result()` bundles evidence in `_aura_meta["evidence"]`.
4. JSON-RPC response returned to External Brain.
5. External Brain generates final reply on external platform.
6. **Verdict**: ❌ **UNVERIFIED BY AURA**. Aura's verifier has no mechanism to intercept the external client's final generation.

---

## 8. Android App Inventory Audit (`android.list_apps`)

1. **Origin**: `android.list_apps` was implemented in Phase 5A and exists in `AppInventory.kt` and `tools/providers/android_provider.py:425`.
2. **Device Mechanism**: Uses Android native `PackageManager.getInstalledApplications()` and `queryIntentActivities()`.
3. **Data Schema**: Returns a list of dictionaries with:
   - `package`: Package name string (e.g. `com.google.android.youtube`).
   - `label`: Human-readable application title (e.g. `YouTube`).
   - `launchable`: Boolean indicating whether a main launcher intent exists.
   - `enabled`: Boolean indicating whether package is active.
4. **Evidence Grounding**: Produces `Evidence(kind=EvidenceKind.OBSERVATION, source="android.package_manager", verified=True)`.
5. **MCP Exposure**: Discovered and exported through `server/routes/agent.py:get_device_registry()` into MCP `tools/list`.
6. **Verdict**: ✅ **FULLY FUNCTIONAL & VERIFIED IN PHASE 5A**.

---

## 9. Security Forensics

| Vector | Finding | Risk Level |
| :--- | :--- | :--- |
| **Arbitrary Shell Execution** | Not exposed in MCP. `ToolRegistry` contains only declared tools. | `SAFE` |
| **Path Traversal (`workspace.*`)** | Enforces `_verify_safe_workspace_path` against `PROJECT_ROOT`. | `SAFE` |
| **Python Sandbox Execution** | Isolated subprocess with timeout and scrubbed env. | `SAFE` |
| **Token Exposure in Logs** | `?token=<TOKEN>` allowed on POST/GET endpoints; logged by Render. | `HIGH` |
| **Auth Fail-Open** | Missing `AURA_AUTH_TOKEN` causes auth to return `"dev"` (unauthenticated). | `HIGH` |
| **Confirmation Bypass** | Standalone bridge runner uses `auto_approve={SAFE, SENSITIVE, DANGEROUS}`. | `HIGH` |
| **Timing Attacks on Auth** | Uses standard `==` string equality instead of `secrets.compare_digest`. | `LOW` |
| **Cross-Origin / CORS** | Managed by FastAPI CORS middleware in `server/main.py`. | `SAFE` |

---

## 10. Test Forensics

### Test Breakdown:
1. **Targeted Tests** (`tests/test_mcp_gateway.py`):
   - 8 tests passing.
   - **Limitation**: Uses FastAPI `TestClient` (synchronous in-memory). Does not test physical network sockets, reverse proxies, or external MCP clients.
2. **Provider Mock Tests** (`tests/test_chatgpt_provider.py`):
   - 7 tests passing.
   - **Limitation**: 100% mocked via `patch.object(provider, "_send")`. Zero live calls to OpenAI API.
3. **Evidence Mock Tests** (`tests/test_chatgpt_evidence_verification.py`):
   - 4 tests passing.
   - **Limitation**: Manually constructs `EvidenceLedger` instances. Does not exercise the MCP Gateway or `ChatGPTProvider` pipeline.
4. **Full Python Regression Suite**:
   - 147 targeted/cloud provider tests executed and passing.
   - Total historical suite contains ~3,420 tests across the entire repository.
5. **Android JVM Unit Suite**:
   - 22 tasks executed (`:app:testDebugUnitTest`), 100% passing.
6. **Live Cloud Tests**:
   - Remote probe `curl https://aura-xwm4.onrender.com/api/mcp/tools` returns HTTP 404.
   - Render deployment of `d3269b1` is **NOT VERIFIED LIVE**.

---

## 11. Architectural Regression Audit

Did commit `d3269b1` break or weaken existing Aura subsystems?

| Subsystem | Impact | Evidence |
| :--- | :--- | :--- |
| **Memory Subsystem** | None | No modifications to `memory/`. |
| **ToolExecutor Core** | None | `tools/executor.py` unmodified. |
| **ResponseVerifier Core** | None | `brain/verify/` unmodified. |
| **Android Companion App** | None | Zero Android source files changed in commit. |
| **Cloud Failover Chain** | None | `ChatGPTProvider` added additively to router dictionaries. |
| **Settings Store** | None | Added `llm.chatgpt_model` strictly conforming to `ALLOWED` rules. |

---

## 12. Production Blockers

1. **`SEC-MCP-001` (HIGH)**: `scripts/run_mcp_bridge.py` fallback path executes tools with `auto_approve={SAFE, SENSITIVE, DANGEROUS}` without confirmation.
2. **`SEC-AUTH-002` (HIGH)**: `POST /api/mcp` and `GET /api/mcp/tools` allow `?token=...` query parameters, leaking credentials into Render/Cloudflare logs.
3. **`SEC-AUTH-003` (HIGH)**: `_authenticate_mcp_request()` fails open when `settings.auth_token` is empty.
4. **`DEPLOY-001` (BLOCKER for Cloud Verification)**: Render Cloud has not deployed commit `d3269b1`. MCP endpoints on Render return HTTP 404.

---

## 13. Minimal Fix Plan (Ordered by Technical Dependency)

1. **Harden MCP Authentication (`server/routes/mcp.py`)**:
   - Remove `token: Optional[str] = Query(None)` from `mcp_direct_jsonrpc`, `mcp_list_tools_rest`, and `mcp_sse_post_message`. Retain query token *only* on `mcp_sse_transport` (`GET /api/mcp/sse`).
   - Use `secrets.compare_digest` for token comparison.
   - Enforce fail-closed: if `settings.auth_token` is empty and environment is not explicitly test/dev, raise HTTP 500 / 401.
2. **Enforce Strict ToolPolicy in MCP Fallback (`server/routes/mcp.py`)**:
   - Change `auto_approve` in `execute_mcp_tool` fallback from `{SAFE, SENSITIVE, DANGEROUS}` to `{ToolRisk.SAFE}` only.
3. **Add Reasoning Model Adaptation in `ChatGPTProvider` (`brain/providers/chatgpt.py`)**:
   - Detect if model starts with `o1` or `o3`. If so, omit `temperature` and convert `role: "system"` to `role: "developer"`.
4. **Embed Truthfulness Guidance in MCP Output Schema (`server/routes/mcp.py`)**:
   - In `_format_mcp_result()`, prepend an explicit evidence statement in `content[0]["text"]`:
     `"[AURA DEVICE EVIDENCE: VERIFIED]"`, `"[AURA DEVICE EVIDENCE: UNVERIFIED]"`, or `"[AURA EXECUTION FAILED: <reason> - DO NOT CLAIM SUCCESS]"`.
     This grounds the external model's prompt in verifiable facts.
5. **Verify Render Cloud Deployment**:
   - Monitor Render deployment logs until commit `d3269b1` is live and verify with authenticated curl.

---

## 14. Confidence & Unknowns

- **Confidence**: 100% on codebase inspection, git commit contents, and static execution paths.
- **Unknown**: Exact deployment schedule and build log of the Render Cloud Docker container (out-of-band on Render.com). Marked as **NOT VERIFIED LIVE**.
