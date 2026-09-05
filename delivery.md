# AURA Capability Grounding Architecture - DELIVERED

The required capability grounding architecture has been implemented.

## 1. Architecture summary
The architecture shifts AURA from inferring its own capabilities to querying a centralized **Capability Registry** before any action.
The ToolExecutor is now wrapped with a esolve_capability() evaluation that validates the actual availability, health, and permission status of the capability the tool implements. Tool execution results are enriched with explicit capability and uthorization attributes, which are formatted into JSON outputs. The core LLM system prompt is updated with strict anti-hallucination rules.
Legacy capability fallbacks have been removed: if a tool lacks a registered capability, it is blocked with NOT_IMPLEMENTED and execution fails.

## 2. Capability system summary
Capabilities are formalized into Capability models located in core/capabilities/models.py. 
Each capability tracks its state across dimensions like:
- vailability_state (e.g., AVAILABLE, BLOCKED_PERMISSION, UNHEALTHY, NOT_IMPLEMENTED)
- equired_permissions, equired_tools, equired_dependencies
The states are resolved dynamically by esolve_capability(capability_id) in core/capabilities/__init__.py. 

## 3. Skill discovery architecture
core.capabilities.discovery implements a semantic SkillDiscovery service that ranks capabilities based on user intent and filters out non-executable candidates, ensuring AURA executes the best *available* skill rather than hallucinating an unavailable one.

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
- core/capabilities/models.py, egistry.py, permissions.py, health.py, __init__.py, discovery.py, dapters.py, actory.py (NEW)
- 	ools/base.py (Modified: Enriched ToolResult JSON output, removed capability: str from base class to enforce explicit declarations).
- 	ools/executor.py (Modified: Integrated esolve_capability into execute() and catalogue(), removed legacy bypass).
- 	ools/builtins/*.py (Modified: explicitly mapped all tools to capabilities).
- 	ools/providers/android_provider.py (Modified: mapped Android tools to capabilities).
- prompts/system.md (Modified: Added strict capability-grounding rules to the agent prompt).
- server/main.py, server/routes/capabilities.py (Modified: Added /api/capabilities inventory route).
- 	ests/test_capabilities.py (NEW)
- 	ests/conftest.py (Modified: Patched mock tools to register capabilities so unit tests don't fail).

## 9. New dependencies
No new third-party dependencies were introduced to keep the core stable and avoid "dependency piles".

## 10. Tests added/modified
- Unit tests added in 	ests/test_capabilities.py covering: tool without capability blocked, unknown capability blocked, missing permissions blocked, unhealthy dependency blocked, healthy execution, discovery ranking, MCP external registration, and runtime capability inventory.
- Existing tool unit tests were patched so test mock tools (e.g. EchoTool, TouchTool) properly register their capabilities in the registry.

## 11. Test results
The core test suite (including 	est_tools.py, 	est_tool_calling.py, 	est_capabilities.py) passes successfully, maintaining AURA's stability. (Note: A few pre-existing background task asyncio bugs from 	est_settings_api.py remain from main branch).

## 12. Remaining limitations
- External adapters (OpenViking, MCP) are scaffolded in core/capabilities/adapters.py but require actual connection implementations.

## 13. Exact capabilities AURA can now execute
- system.time
- system.info
- system.processes
- desktop.windows
- desktop.input
- desktop.applications
- desktop.commands
- ision.capture
- ision.describe
- ilesystem.read
- ilesystem.write
- memory.write
- chat.react

## 14. Exact capabilities still unavailable
Capabilities blocked by missing environmental dependencies (e.g., Android bridge disconnected, Windows GDI unavailable) will now be accurately reported as UNHEALTHY or BLOCKED_PERMISSION directly to the LLM (with explicit JSON reasons) rather than hallucinated as functional.

## 15. Known failure modes
If an external developer creates a custom Tool plugin but forgets to define capability and register it, ToolExecutor will now hard-block it as NOT_IMPLEMENTED.
