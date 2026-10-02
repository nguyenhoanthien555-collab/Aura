# AURA FORENSIC FIX REPORT
**Auditor & Fix Engineer**: Antigravity Forensic Engineering  
**Date**: 2026-10-02  
**Target Repository**: `d:\AURA` on branch `feature/aura-identity`  
**Base Commit**: `d3269b1ecf85b61e7b09cf279ac724ef759ba005`  
**Reference Documents**: `AURA_FORENSIC_AUDIT.md`, `AURA_MASTER_PLAN_CHATGPT_MAIN_BRAIN.md`  

---

## 1. Executive Summary

This fix pass systematically resolved the security vulnerabilities, credential leak vectors, protocol incompatibilities, and architectural concerns identified in `AURA_FORENSIC_AUDIT.md`.

In strict adherence to **RULE 1 (NO REWRITE)** and **RULE 2 (CRITICAL SECURITY FIRST)**:
- Core systems (`ToolExecutor`, `Evidence`, `EvidenceLedger`, `ResponseVerifier`, Android Native Handset) were preserved without architectural upheaval.
- All modifications were targeted, minimal, and verifiable.
- A critical latent bug in `execute_mcp_tool()` (unpacked kwargs `**arguments` passed to `ToolExecutor.execute`) was discovered and remediated.
- Zero git commits, zero git pushes, and zero cloud deploys were executed in this pass.

---

## 2. Remediated Forensic Findings

### Finding 1: Dangerous MCP Auto-Approval in Standalone Bridge (`SEC-MCP-001`)
- **Severity**: HIGH
- **Files Modified**: `server/routes/mcp.py:150-166`
- **Vulnerability**: When running standalone without an active `ServerRuntime` (e.g. running `python scripts/run_mcp_bridge.py` standalone), the fallback policy hardcoded `auto_approve={SAFE, SENSITIVE, DANGEROUS}`, completely bypassing confirmation for dangerous tools (e.g. writing files, arbitrary command execution, system modifications).
- **Remediation**:
  - Replaced the permissive auto-approve set with `auto_approve=frozenset({ToolRisk.SAFE})`.
  - In standalone / headless MCP mode without interactive confirmation channels, any `SENSITIVE` or `DANGEROUS` tool execution fails closed through native `ToolExecutor` checks:
    - Returns `ok=False`
    - `status="DENIED"`
    - `error_code="CONFIRMATION_REQUIRED"`
    - `execution="not_attempted"`
  - Fixed argument passing contract: changed `executor.execute(name, **arguments)` to `executor.execute(name, arguments)`.
- **Verification**: `tests/test_mcp_gateway.py::test_mcp_dangerous_tool_denied_in_fallback` (PASSED).

---

### Finding 2: Token Exposure via Query Parameter (`SEC-AUTH-002`)
- **Severity**: HIGH
- **Files Modified**: `server/routes/mcp.py:35-80, 325-450`
- **Vulnerability**: Query parameter authentication `?token=<TOKEN>` was accepted across all MCP endpoints (`POST /api/mcp`, `GET /api/mcp/tools`, `POST /api/mcp/messages`, `GET /api/mcp/sse`). Query parameters are recorded in plaintext in Render Cloud access logs, reverse proxies, Cloudflare edge logs, and browser histories.
- **Remediation**:
  - Removed `token` query parameters from the signatures of `POST /api/mcp`, `GET /api/mcp/tools`, and `POST /api/mcp/messages`.
  - Enforced mandatory `Authorization: Bearer <token>` on all POST and REST endpoints.
  - Restricted query parameter extraction strictly to `GET /api/mcp/sse` (`allow_query_token=True`), which is the only endpoint where standard browser `EventSource` cannot transmit custom headers.
  - Upgraded token comparison from `==` to constant-time `secrets.compare_digest` to prevent timing attacks.
- **Verification**: `tests/test_mcp_gateway.py::test_mcp_endpoint_auth` (asserts 401 on `POST /api/mcp?token=...` and `GET /api/mcp/tools?token=...`, asserts 200 on Bearer header, and permits token strictly on `allow_query_token=True` transports).

---

### Finding 3: Auth Fail-Open When `settings.auth_token` Is Empty (`SEC-AUTH-003`)
- **Severity**: HIGH
- **Files Modified**: `server/routes/mcp.py:43-62`
- **Vulnerability**: `_authenticate_mcp_request()` returned `"dev"` whenever `settings.auth_token` was blank, permitting unauthenticated public internet requests to execute tools if the server started with a missing environment variable.
- **Remediation**:
  - Implemented fail-closed behavior: if `settings.auth_token` is empty, the endpoint raises HTTP 500 (`"Aura authentication token is not configured on this server."`).
  - Allowed unauthenticated requests strictly when `settings.allow_insecure` is explicitly set via the established environment variable `AURA_ALLOW_INSECURE=1` (`core/config.py`).
- **Verification**: `tests/test_mcp_gateway.py::test_mcp_auth_fail_closed` (asserts HTTP 500 when token is unset and `AURA_ALLOW_INSECURE` is absent).

---

### Finding 4: Grounding and Truthful Evidence for External MCP Clients (`ARCH-VERIF-004`)
- **Severity**: HIGH
- **Files Modified**: `server/routes/mcp.py:168-230`
- **Vulnerability**: When Aura functions as an MCP Tool Server, external models (ChatGPT, Claude Desktop, Cursor) render final answers client-side, bypassing Aura's `ResponseVerifier`. If a tool fails or is denied, the external model could hallucinate success.
- **Remediation**:
  - Updated `_format_mcp_result()` to embed unambiguous, machine-readable evidence banners in the returned `content[0]["text"]`:
    - On refusal / failure:
      ```text
      [AURA EXECUTION REFUSED/FAILED]
      Status: <STATUS>
      Error: [<CODE>] <MESSAGE>
      Notice: This action did NOT succeed. Do not state to the user that it succeeded.
      ```
    - On verified physical postconditions:
      ```text
      [AURA EVIDENCE: Verified physical postcondition on device]
      ```
  - Preserved structured metadata in `_aura_meta["evidence"]` and `_aura_meta["status"]`.
- **Verification**: `tests/test_mcp_gateway.py::test_mcp_evidence_formatting` (PASSED).

---

### Finding 5: Reasoning Model Incompatibility (`LLM-COMPAT-005`)
- **Severity**: MEDIUM
- **Files Modified**: `brain/providers/chatgpt.py`
- **Vulnerability**: `ChatGPTProvider` inherited `OpenAICompatibleProvider`, which unconditionally passed `temperature` and `{"role": "system", ...}`. OpenAI reasoning models (`o1`, `o3-mini`) reject custom temperature parameters and require the `developer` role instead of `system`.
- **Remediation**:
  - Added `is_reasoning_model` property detecting model identifiers starting with `o1` or `o3`.
  - Overrode `_payload()` and `_send()` to automatically strip `temperature` and convert `role: "system"` to `role: "developer"` when targeting reasoning models.
  - Added capability guard in `generate_with_tools()` to reject models that do not support tools (`o1-preview`, `o1-mini`) with a descriptive `ProviderUnavailableError`.
  - Added explicit architectural docstrings clarifying that `ChatGPTProvider` is an OpenAI Chat Completions REST API client (`api.openai.com`), not a web automation or consumer desktop reverse-engineering session.
- **Verification**: `tests/test_chatgpt_provider.py` (added 3 new test cases: `test_chatgpt_reasoning_model_payload_adaptation`, `test_chatgpt_reasoning_model_send_adaptation`, and `test_chatgpt_unsupported_reasoning_tools_rejected`).

---

### Finding 6: Unbounded In-Memory SSE Session Queues (`RES-LEAK-006`)
- **Severity**: LOW
- **Files Modified**: `server/routes/mcp.py:38-42, 370-455`
- **Vulnerability**: `_sse_sessions` maintained unbounded `asyncio.Queue` references without TTL tracking, risking memory leaks on 512MB Render containers during aborted connections.
- **Remediation**:
  - Stored `_sse_sessions[session_id] = (queue, last_active_timestamp)`.
  - Automatically pruned sessions idle for more than 30 minutes during client connections.
  - Refreshed timestamps on incoming message posts and explicitly removed sessions on SSE client disconnect in the `finally:` block.
- **Verification**: `tests/test_mcp_gateway.py::test_mcp_endpoint_auth` and session routing tests.

---

## 3. Verification & Evidence Table

All targeted, provider, regression, and Android JVM suites were executed:

| Test Suite / Command | Scope | Result | Execution Time |
| :--- | :--- | :--- | :--- |
| `tests/test_mcp_gateway.py` | MCP Gateway, Auth, Fallback Policy, Evidence | **11 / 11 PASSED** (100%) | 8.94s |
| `tests/test_chatgpt_provider.py` | ChatGPT Provider, Reasoning Models, Tool Rejection | **10 / 10 PASSED** (100%) | 0.86s |
| `tests/test_chatgpt_evidence_verification.py` | Claim -> Evidence Ledger Integration | **4 / 4 PASSED** (100%) | 0.59s |
| `tests/test_cloud_providers.py` | Full Cloud Provider Suite | **101 / 101 PASSED** (100%) | 6.87s |
| `tests/test_provider_resolution.py` | Router Provider Resolution & Model Routing | **27 / 27 PASSED** (100%) | 0.16s |
| **Total Python Regression Group** | **All 5 Interconnected Suites** | **153 / 153 PASSED** (100%) | **17.26s** |
| `gradlew.bat :app:testDebugUnitTest` | Full Android Kotlin / Compose Test Suite | **22 / 22 TASKS PASSED** (100%) | 8.0s |

---

## 4. Modified Files Summary

```text
modified:   brain/providers/chatgpt.py
modified:   server/routes/mcp.py
modified:   tests/test_chatgpt_provider.py
modified:   tests/test_mcp_gateway.py
modified:   .Codex/current-task.md
modified:   .Codex/progress.md
```

No git commit was made. No git push was made. No deployment was initiated.

---

## 5. Architectural Integrity Statement

1. **ToolExecutor Invariant**: The single gate of execution (`ToolExecutor`) remains the only authority for executing PC, Workspace, and Android tools.
2. **Device Bridge Invariant**: Device tasks on the physical Oppo handset (`CPH2251`) continue to be mediated via `GatewayDeviceBridge` long-polling with cryptographic tokens.
3. **Evidence Integrity**: Physical postconditions verified on device continue to be recorded as `Evidence(kind=POSTCONDITION, verified=True)`. In internal conversation mode, `ResponseVerifier` enforces claim validity; in external MCP mode, CallToolResult outputs ground the model in verifiable evidence statements.
4. **Safety Defaults**: Standalone MCP execution now strictly fails closed (`auto_approve={SAFE}`) for any unapproved, sensitive, or dangerous capability.
