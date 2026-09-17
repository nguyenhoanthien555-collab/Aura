# AURA 2.0 — Standalone Distribution & Deployment Specification
**Status:** IMPLEMENTED | TESTED | LIVE VERIFIED
**Document Version:** 1.0.0
**Classification:** Deployment & Operations

---

## 1. Executive Summary

AURA 2.0 is distributed as a fully standalone, self-contained local AI system. It requires zero cloud API keys, zero external network connectivity, and zero third-party agent frameworks to operate at full capability.

This document details the minimal hardware requirements, packaging structure, deployment procedures, and certification testing for standalone installations.

---

## 2. Hardware Tier Specifications

AURA adapts dynamically to host hardware discovered at startup:

### Tier 1: Minimal (CPU Only / Edge Devices)
- **Target Systems:** Laptops, mini-PCs, or edge servers without discrete GPUs.
- **CPU:** 4+ physical cores (x86_64 or ARM64).
- **RAM:** 8 GB – 12 GB.
- **VRAM:** 0 GB (CPU inference).
- **Recommended Model:** 3B–4B parameter quantized GGUF (e.g. Llama-3.2-3B-Instruct-Q4_K_M, Phi-3.5-mini-instruct-Q4_K_M) or built-in DeterministicBackend.
- **Operational Profile:** Concurrency limit: 1 background worker; Context length: 2,048 tokens.

### Tier 2: Recommended (Consumer Workstations / Laptops)
- **Target Systems:** Modern developer workstations or gaming laptops.
- **CPU:** 8+ physical cores.
- **RAM:** 16 GB – 31 GB.
- **VRAM:** 6 GB – 10 GB (NVIDIA RTX 3060/4060 or Apple M-series Unified Memory).
- **Recommended Model:** 7B–8B parameter quantized GGUF (e.g. Meta-Llama-3.1-8B-Instruct-Q4_K_M, Qwen2.5-7B-Instruct-Q5_K_M).
- **Operational Profile:** Concurrency limit: 2 background workers; Context length: 4,096 tokens.

### Tier 3: Power (Dedicated AI Workstations / Local Servers)
- **Target Systems:** High-end desktop workstations or on-premises rack servers.
- **CPU:** 12+ physical cores.
- **RAM:** 32+ GB.
- **VRAM:** 12+ GB (NVIDIA RTX 3090/4090, RTX A5000/A6000).
- **Recommended Model:** 14B–32B parameter models (e.g. Qwen2.5-14B-Instruct-Q4_K_M, DeepSeek-R1-Distill-Qwen-14B).
- **Operational Profile:** Concurrency limit: 4 background workers; Context length: 8,192+ tokens.

---

## 3. Distribution Directory Structure

When unpacked on a target host, AURA organizes its filesystem predictably:

`	ext
AURA/
├── data/
│   ├── aura/
│   │   ├── brains/              # Local Brain packages & manifests
│   │   │   ├── manifest.json    # Active Brain pointer & package catalog
│   │   │   └── aura-local-v1/   # Default local model package
│   │   ├── learning/
│   │   │   ├── datasets/        # Experience-curated training datasets (JSONL)
│   │   │   └── jobs/            # Local training job runs and artifacts
│   │   └── sqlite/
│   │       └── aura.db          # Durable tasks, experiences, and audit state
│   └── temp/
├── docs/                        # Architecture & operational documentation
├── scripts/
│   ├── verify_aura_local_ai_live.py # 25-scenario live verification suite
│   └── run_server.py            # Main server entrypoint
└── server/                      # FastAPI HTTP & WebSocket server
`

---

## 4. Zero-Cloud Installation & Startup

### Step 1: Environment Configuration
Create a .env file or export environment variables:
`ash
# Force offline mode (blocks external sockets at the OS socket layer)
AURA_OFFLINE=1

# Local inference server endpoint (if using external llama.cpp / Ollama)
AURA_INFERENCE_ENDPOINT=http://127.0.0.1:11434

# Data and Brain directories
AURA_DATA_DIR=./data/aura
AURA_BRAINS_DIR=./data/aura/brains
`

### Step 2: Database & Brain Initialization
On initial launch, AURA automatically detects absent tables and initializes:
- 	asks and 	ask_steps tables for durable execution.
- ura_experience_records for experiential learning.
- rain_version_records and 	raining_job_records for model tracking.
- A default ura-local-v1 brain package if no existing brain is registered.

### Step 3: Launching the Daemon & Server
`ash
# Start AURA 2.0 with background supervisor and local inference
python scripts/run_server.py
`

---

## 5. System Verification & Certification

Every standalone installation should be validated using the automated live verification harness:

`ash
python scripts/verify_aura_local_ai_live.py
`

### Mandatory 25 Certification Scenarios:
1. LIVE-1: Local Brain loads and transitions to ACTIVE.
2. LIVE-2: Full operational capacity with zero cloud credentials.
3. LIVE-3: Local conversation completes with correct AURA identity.
4. LIVE-4: Local Brain discovers and inspects registered tool catalogue.
5. LIVE-5: Local Brain selects and formulates safe tool calls.
6. LIVE-6: Tool results are captured as authoritative Evidence items.
7. LIVE-7: Deterministic Verifier assesses actual world state.
8. LIVE-8: Response synthesis reports tool outcomes honestly.
9. LIVE-9: Natural-language durable task creation and multi-step decomposition.
10. LIVE-10: Autonomous tool synthesis engine ast-checks and sandbox-validates code.
11. LIVE-11: Android tool actions route through device bridge.
12. LIVE-12: Socket intercepts block non-localhost network connections.
13. LIVE-13: Provenance metadata accurately tracks local brain ID and version.
14. LIVE-14: Execution experience persists to SQLite store.
15. LIVE-15: Experience store filters and exports learning candidate dataset.
16. LIVE-16: Training job runner executes isolated candidate training.
17. LIVE-17: Immutable Reference Evaluation Suite grades model across 8 benchmarks.
18. LIVE-18: Regressed or defective candidate brain is rejected.
19. LIVE-19: High-performing candidate brain is atomically promoted.
20. LIVE-20: Previous brain package is safely and instantaneously rolled back.
21. LIVE-21: 24/7 daemon runs continuously across client disconnects.
22. LIVE-22: Durable tasks survive process termination and reboot.
23. LIVE-23: Interrupted tasks resume without replaying completed mutations.
24. LIVE-24: Learning worker resumes unprocessed experiences after restart.
25. LIVE-25: Privacy screening redacts and excludes secrets from learning eligibility.
