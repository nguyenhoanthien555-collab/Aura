# Skill: aura-agent-runtime
## Role: AURA Agent Execution Loop Specialist
### Guidelines:
- Intent Extraction -> Tool Resolution -> Sandbox Execution -> Result Synthesis.
- Memory integration: Store conversation turns in persistent SQLite memory store.
- Tool Call Safety: Wrap every tool execution in `tools/timeout.py`.
