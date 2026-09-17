# AURA 2.0 — Local Brain Architecture Specification
**Status:** IMPLEMENTED | TESTED | LIVE VERIFIED
**Document Version:** 1.0.0
**Classification:** Core System Architecture

---

## 1. Executive Summary

AURA (Autonomous Universal Runtime Architecture) 2.0 establishes a genuinely local-first personal artificial intelligence named **AURA**. Unlike agent architectures that treat local models as fallback options or thin shims over cloud providers, AURA's primary cognitive engine is its **Local Brain**. Cloud APIs remain strictly optional external capabilities that can be severed entirely without compromising core autonomous agency, tool orchestration, or state durability.

---

## 2. Core Architectural Principles

1. **Local-First Default Intelligence:**
   All cognitive processing—conversation, tool selection, plan generation, and postcondition reasoning—defaults to the locally hosted model runtime. Cloud endpoints are never queried unless explicitly configured and permitted.
2. **Model Output is Untrusted:**
   AURA enforces an absolute boundary between LLM output and verified reality. Model tokens are candidate proposals; only the ToolExecutor, EvidenceLedger, and Verifier establish authoritative world state.
3. **Deterministic Immutability:**
   Local Brain packages are immutable artifacts accompanied by cryptographically verified manifests (SHA-256 digests). Model updates occur exclusively through atomic promotions.
4. **Complete Offline Enforceability:**
   When AURA_OFFLINE=1 is set, all socket attempts outside localhost (127.0.0.1, localhost, ::1) are blocked at the socket layer. Zero external network packets are emitted.

---

## 3. Hardware Profiling & Adaptive Tiering

Located in [rain/hardware.py](file:///D:/AURA/brain/hardware.py), AURA inspects host hardware at startup to determine optimal local inference parameters and recommended model sizes.

### Hardware Discovery Pipeline
- **CPU:** Physical core count and total logical threads via psutil or os.cpu_count().
- **System Memory:** Available and total RAM in gigabytes.
- **GPU Acceleration:**
  - Invokes 
vidia-smi queries for NVIDIA GPU name, driver version, CUDA version, and dedicated VRAM.
  - Windows 
vml.dll / ctypes fallback for direct driver query if CLI tools are unavailable.
  - Returns cuda, mps, or cpu backend target.

### Tier Classification Matrix

| Hardware Tier | Memory / VRAM Profile | Recommended Model Format | Context Window | Concurrency Bound |
|---|---|---|---|---|
| **Tier 1 (Minimal)** | < 12 GB RAM, 0 GB VRAM | 3B–4B GGUF Q4_K_M (CPU) or Deterministic | 2,048 tokens | 1 task worker |
| **Tier 2 (Recommended)** | 16–31 GB RAM, 6–10 GB VRAM | 7B–8B GGUF Q4_K_M / Q5_K_M (CUDA) | 4,096 tokens | 2 task workers |
| **Tier 3 (Power)** | ≥ 32 GB RAM, ≥ 11 GB VRAM | 14B–32B GGUF Q4_K_M or FP16 (CUDA) | 8,192 tokens | 4 task workers |

---

## 4. Brain Package & Lifecycle Management

Located in [rain/package.py](file:///D:/AURA/brain/package.py), BrainManager controls local model packages, storage layout, status transitions, and atomic swaps.

### Package Storage Layout
`	ext
data/aura/brains/
├── manifest.json                  # Active and candidate package registry
├── aura-local-v1/
│   ├── manifest.json              # Package metadata and SHA-256 checksums
│   ├── weights.bin                # Model weights (or GGUF container)
│   └── tokenizer.json             # Local tokenizer configuration
└── candidate-aura-v1.1/
    ├── manifest.json
    └── weights.bin
`

### Manifest Schema (BrainManifest)
- rain_id: Unique identifier (e.g. ura-local-v1).
- ersion: Semantic version (e.g. 1.0.0).
- model_format: Format (gguf, safetensors, deterministic, http).
- created_at: ISO-8601 UTC timestamp.
- status: Lifecycle state (ACTIVE, CANDIDATE, EVALUATING, DEPRECATED, ARCHIVED).
- parent_brain_id: Provenance parent ID.
- 	raining_job_id: Job ID that generated this candidate.
- weights_checksum: SHA-256 digest of weights file.
- eval_score: Benchmark evaluation score (0.0 to 1.0).
- parameters_count: Model parameter count (e.g. 7B).
- quantization: Quantization level (e.g. Q4_K_M).
- context_length: Maximum context window.
- metadata: Custom metadata.

### Atomic Promotion and Safe Rollback Semantics
1. **Thread-Safe Mutex (	hreading.RLock):** All registry reads, promotions, and rollbacks are protected by re-entrant mutexes.
2. **Atomic Symlink / Pointer Swaps:** State promotion updates the ctive_brain_id pointer atomically. If promotion succeeds, previous active brain is preserved as fallback.
3. **Instantaneous Rollback:** Calling manager.rollback() immediately restores the prior active brain and sets the regressed brain to DEPRECATED.

---

## 5. Local Runtime & Inference Backends

Located in [rain/local_runtime.py](file:///D:/AURA/brain/local_runtime.py), LocalModelRuntime manages active model instances and delegates execution to appropriate backend engines:

### 1. DeterministicBackend
- Designed for hermetic verification, unit testing, CI/CD pipelines, and air-gapped environments without GPU hardware.
- Performs rule-based intent parsing, tool selection against the live tool catalogue, and honest postcondition formulation without non-deterministic hallucinations.
- Fully supports generate, stream, and generate_with_tools.

### 2. HttpInferenceBackend
- Connects to local high-performance inference servers (e.g., Ollama, llama.cpp server, vLLM) over local loopback (http://127.0.0.1:11434 or custom port).
- Implements standard OpenAI/Ollama compatible endpoints (/api/generate, /api/chat, /v1/chat/completions).
- Enforces strict socket bounding to loopback interfaces.

---

## 6. LocalAuraBrain Provider

Located in [rain/providers/local_aura.py](file:///D:/AURA/brain/providers/local_aura.py), LocalAuraBrain registers as the primary provider in BrainRouter and implements the standard provider interface:
- Self-identifies exclusively as **AURA**.
- Never claims actions succeeded without confirming evidence.
- Emits structured tool calls (Turn.tool_calls) directly mapping to registered capabilities.

---

## 7. Provenance Tracking

Located in [rain/provenance.py](file:///D:/AURA/brain/provenance.py), every completion carries verifiable provenance:
- provider: local_aura
- rain_id: e.g. ura-local-v1
- ersion: e.g. 1.0.0
- model_digest: SHA-256 digest of active weights
- ackend: cuda, cpu, deterministic, or http
- 	emperature: Sampling temperature
- 	imestamp: ISO-8601 UTC timestamp
- metadata: Additional execution trace data

---

## 8. Capability Routing & Offline Enforcement

- In [rain/providers/capabilities.py](file:///D:/AURA/brain/providers/capabilities.py), local_aura is registered with unction_calling=True and streaming=True.
- In [rain/router.py](file:///D:/AURA/brain/router.py), router configuration prioritizes local_aura as the primary model.
- When AURA_OFFLINE=1 is present:
  - BrainRouter.active_chain() forces execution through local_aura.
  - Attempts to route to cloud providers fail immediately and safely with an offline policy refusal.
