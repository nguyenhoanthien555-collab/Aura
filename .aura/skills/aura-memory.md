# Skill: aura-memory
## Role: AURA Long-Term & Short-Term Memory Manager
### Structure:
- Short-term: In-memory sliding window for active session turns.
- Long-term: SQLite/vector database for facts, preferences, and project state.
- Privacy: Never store secrets, API keys, or raw credentials in memory.
