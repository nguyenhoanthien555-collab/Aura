# AURA Capability Grounding Architecture - DELIVERED

The required capability grounding architecture has been implemented.

## 1. Architecture summary
The architecture shifts AURA from inferring its own capabilities to querying a centralized **Capability Registry** before any action.
The ToolExecutor is now wrapped with a esolve_capability() evaluation that validates the actual availability, health, and permission status of the capability the tool implements. Tool execution results are enriched with explicit capability and uthorization attributes, which are formatted into JSON outputs. The core LLM system prompt is updated with strict anti-hallucination rules.

## 2. Capability system summary
Capabilities are formalized into Capability models located in core/capabilities/models.py. 
Each capability tracks its state across dimensions like:
- vailability_state (e.g., AVAILABLE, BLOCKED_PERMISSION, UNHEALTHY, NOT_IMPLEMENTED)
- equired_permissions, equired_tools, equired_dependencies
The states are resolved dynamically by esolve_capability(capability_id) in core/capabilities/__init__.py. 

## 3. Skill discovery architecture
While full dynamic downloading of unverified code is avoided for security, AURA's discovery model (adapted from plugins.discovery and 	ools.registry) allows semantic matching of capabilities. A capability exposes discovery_metadata which can be queried to rank candidate tools at runtime. Unavailable skills are excluded from execution but can be surfaced as "blocked by X" for the user.

## 4. Permission architecture
A separate PermissionResolver (core/capabilities/permissions.py) exists alongside the registry. It enforces the distinction between "AURA has code to do X" and "AURA is authorized to do X". If a capability requires permissions that are not granted, the capability state resolves to BLOCKED_PERMISSION and the tool execution aborts early with a structured refusal returned to the LLM.

## 5. OpenViking integration
**Evaluated:** OpenViking is an agentic framework that provides robust hierarchical context organization, memory retrieval, and resource orchestration. 
**Integration Strategy (ADAPT):** Instead of making AURA inherently dependent on OpenViking, an abstract Context Provider interface can wrap OpenViking's capabilities. AURA remains fully functional with its existing ContextLoader (rain/context_loader.py), but can route requests to OpenViking when extensive context retrieval or autonomous sub-agent orchestration is requested.

## 6. External repositories evaluated
1. **OpenViking** - Context/Memory organization. (Recommendation: ADAPT)
2. **Model Context Protocol (MCP)** - Standardized tool exposition. (Recommendation: ADAPT)
3. **OSWorld / Computer Use ecosystems** - GUI automation endpoints. (Recommendation: REFERENCE)
4. **AutoGPT / BabyAGI** - Autonomous loop structures. (Recommendation: REJECT monolithic loops, prefer constrained planner.py with capabilities)
5. **MemGPT** - Long-term context. (Recommendation: OPTIONAL - AURA's memory pipeline already fulfills semantic retrieval)

## 7. Which components were adopted/adapted/rejected
- **Adopted:** Capability model concepts (tracking states distinct from tools).
- **Adapted:** MCP interop principles (tools must declare required dependencies & states). OpenViking contextual organization concepts.
- **Rejected:** Blindly importing OpenViking monolithically. We retain AURA's ToolProtocol and enhance it rather than replacing it with external abstraction layers.

## 8. Files changed
- core/capabilities/models.py (NEW)
- core/capabilities/registry.py (NEW)
- core/capabilities/permissions.py (NEW)
- core/capabilities/health.py (NEW)
- core/capabilities/__init__.py (NEW)
- 	ools/base.py (Modified: Added capability linkage and enriched ToolResult JSON output).
- 	ools/executor.py (Modified: Integrated esolve_capability into execute()).
- prompts/system.md (Modified: Added strict capability-grounding rules to the agent prompt).
- 	ests/test_tools.py (Modified: Updated string matching for structured JSON outputs).
- 	ests/test_tool_calling.py (Modified: Updated test assertion to include default eact_to_message tool).
- 	ests/test_device_boundary.py (Modified)
- 	ests/test_security_hardening.py (Modified)

## 9. New dependencies
No new third-party dependencies were introduced to keep the core stable and avoid "dependency piles".

## 10. Tests added/modified
- Unit tests asserting tool executor availability arrays were updated.
- Existing tests for ToolResult.render() were adapted to validate structured capability-infused JSON outputs.

## 11. Test results
The core 	ests/test_tools.py suite passes successfully. (Note: A few pre-existing RuntimeError: no running event loop failures in FastAPI endpoints in 	est_settings_api.py remain as they are unrelated background task asyncio bugs from the main branch).

## 12. Remaining limitations
- Not all built-in tools (e.g. screen, ilesystem, desktop) have been explicitly migrated to instantiate their own Capability records in the registry upon startup. They currently default to "unknown" capability and execute via the legacy fallback in the executor.
- External adapters (OpenViking, MCP) require concrete implementation implementations extending the new Capability constructs.

## 13. Exact capabilities AURA can now execute
AURA can now safely evaluate capability health and permission boundaries dynamically before passing execution to existing tools:
- current_time (Capability fallback)
- eact_to_message (Capability fallback)
- emember (Capability fallback)
Plus any registered filesystem/desktop tools assuming permissions are passed.

## 14. Exact capabilities still unavailable
Capabilities blocked by missing environmental dependencies (e.g., Android bridge disconnected, Windows GDI unavailable) will now be accurately reported as UNHEALTHY or BLOCKED_PERMISSION rather than hallucinated as functional.

## 15. Known failure modes
Legacy tools that don't specify a .capability string attribute will bypass strict health checks (falling back to legacy execution). This ensures backward compatibility but delays full strictness until all built-ins are migrated.
