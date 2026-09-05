# Skill: aura-asyncio
## Role: AURA AsyncIO Concurrency & Task Management
### Rules:
- Never block the event loop with synchronous I/O; use `asyncio.to_thread` for CPU/blocking operations.
- Always handle task cancellation and clean up coroutine references.
- Avoid orphaned coroutines (prevent "coroutine was never awaited" warnings).
