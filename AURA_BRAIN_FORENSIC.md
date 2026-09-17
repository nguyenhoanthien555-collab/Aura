# AURA 2.0 — BRAIN & INFERENCE SUBSYSTEM FORENSIC AUDIT

**Audit Date:** 2026-09-15  
**Auditor:** Independent Forensic AI Architect  
**Repository:** `D:\AURA`  
**Branch:** `feature/aura-identity`  
**Audit Standard:** Zero-Trust Forensic Verification  

---

## 1. Executive Summary: The Central Question

> **"Does AURA actually have its own AI Brain, or does AURA currently provide an abstraction/wrapper around external or deterministic models?"**

### Forensic Verdict
**AURA DOES NOT HAVE ITS OWN AI BRAIN.**

AURA currently provides:
1. An **external cloud API wrapper** for primary execution (`provider: gemini` via `GoogleGenAI` in `brain/providers/gemini.py`).
2. An **in-process rule-based deterministic emulator** for offline operation (`LocalAuraBrain` running `DeterministicBackend` in `brain/local_runtime.py`).
3. An **unwired, dormant HTTP client** (`HttpInferenceBackend`) targeting external local servers (`http://127.0.0.1:11434`), which is never instantiated in production.
4. An **empty directory structure with dummy files** in `brains/`, where model weights consist of 53-byte plain text strings (`b"AURA_BRAIN_CANDIDATE_..."`).

AURA possesses zero proprietary neural weights, zero fine-tuned adapters, and zero native neural inference runtimes.

---

## 2. Answers to Prompt Section 1 Inquiries (Items A – P)

| Item | Forensic Question | Precise Factual Answer | Verifiable Evidence |
| :---: | :--- | :--- | :--- |
| **A** | Does AURA have a model artifact? | **NO real neural artifact exists.** Only JSON manifests and 53-byte dummy ASCII files. | `brains/aura-local-v1/manifest.json`, `brains/aura-local-v1-candidate-*/model.bin` (53 bytes). |
| **B** | Who created that model artifact? | **Synthetic dummy generation** inside `TrainingJobRunner.run_job()`. | `learning/training.py` L191: `f"AURA_BRAIN_CANDIDATE_{job.output_brain_id}_{examples_count}".encode("utf-8")`. |
| **C** | Are actual model weights present? | **ZERO neural weights exist on disk.** Scanned entire drive for `.gguf`, `.safetensors`, `.bin`, `.pt`, `.onnx`. None found. | File scan returned only the 53-byte test text files. |
| **D** | Are those weights trained specifically for AURA? | **NO.** Zero neural training or parameter optimization ever occurred. | `learning/training.py` contains `time.sleep(0.05)` and hardcoded `final_loss: 0.042`. |
| **E** | Can the model run without Ollama? | **YES, but solely because it is a Python `if/elif` string matcher.** | `brain/local_runtime.py` L92–114 runs pure Python string checks in-process. |
| **F** | Can the model run without llama.cpp server? | **YES, via the same deterministic rule engine.** | No llama.cpp binary, shared library, or subprocess is ever invoked. |
| **G** | Can the model run without any HTTP server? | **YES, in-process deterministic simulation.** | `DeterministicBackend` runs entirely in Python memory without sockets. |
| **H** | Can the model run without Cloud APIs? | **YES, but only with deterministic string responses.** | In offline mode, queries return hardcoded templates or `"Aura local brain response to: ..."`. |
| **I** | Can AURA load the model directly? | **NO neural model is loaded.** `load()` simply sets an in-memory pointer `self.package = package`. | `DeterministicBackend.load()` in `brain/local_runtime.py` L72–76. |
| **J** | Can AURA generate tokens locally? | **NO probabilistic token generation exists.** Words are split by space from static strings. | `DeterministicBackend.stream()` in `brain/local_runtime.py` L115–120: `full_text.split(" ")`. |
| **K** | Is `LocalAuraBrain` actually doing inference? | **NO.** It performs pattern matching and template string interpolation. | `brain/local_runtime.py` L101–114. |
| **L** | Or is it routing to another service? | In cloud mode it routes to Gemini/Groq/Mistral. In offline mode it routes to `DeterministicBackend`. | `brain/router.py` L45–95. |
| **M** | Does the model understand tool protocol intrinsically? | **NO.** Tool selection in local mode is hardcoded regex; in cloud mode it is prompt-directed JSON. | `brain/local_runtime.py` L168–175 matches `"calculator"` -> `android.launch_app`. |
| **N** | Or is that behavior produced by prompts/rules? | **100% produced by Python rules (local) or system prompts (cloud).** | `DeterministicBackend.generate_with_tools()`. |
| **O** | Does AURA possess a trained identity? | **NO.** Zero identity is embedded in model weights. | Identity strings reside in Python source code and `system_prompt.py`. |
| **P** | Or is identity entirely prompt/config based? | **100% prompt- and rule-based.** | Hardcoded string in `brain/local_runtime.py` L102: `"Tôi là AURA, trợ lý cá nhân thông minh..."`. |

---

## 3. Detailed Call Graph: `LocalAuraBrain` Execution

When a user prompt reaches `LocalAuraBrain`, here is the exact runtime trace:

```
[User Message: "Who are you?"]
       │
       ▼
LocalAuraBrain.generate(prompt="Who are you?")
(File: brain/providers/local_aura.py, L78)
       │
       ▼
LocalModelRuntime.generate(prompt="Who are you?")
(File: brain/local_runtime.py, L150)
       │
       ▼
DeterministicBackend.generate(prompt="Who are you?")
(File: brain/local_runtime.py, L92)
       │
       ▼
clean = prompt.lower().strip()
if "who are you" in clean:
    return "Tôi là AURA, trợ lý cá nhân thông minh và an toàn của bạn."
       │
       ▼
[Return String: "Tôi là AURA, trợ lý cá nhân thông minh và an toàn của bạn."]
```

### Key Forensic Code Evidence:
```python
# File: brain/providers/local_aura.py
class LocalAuraBrain(LLMProvider):
    def __init__(self, runtime=None, manager=None, auto_load=True):
        self.manager = manager or BrainManager()
        self.runtime = runtime or LocalModelRuntime()  # <--- Instantiates default LocalModelRuntime
        ...

# File: brain/local_runtime.py
class LocalModelRuntime:
    def __init__(self, backend: Optional[InferenceBackend] = None, hardware=None):
        self.hardware = hardware or detect_hardware()
        self.backend = backend or DeterministicBackend(self.hardware)  # <--- Defaults to DeterministicBackend!
```

**Observation:** `LocalAuraBrain` has no logic to automatically detect, download, or spin up a real neural engine. By default, it always instantiates `DeterministicBackend`.

---

## 4. Model Artifact & Filesystem Forensic Analysis

### Model Search Inventory
A recursive scan of the repository and system storage for model weights was conducted:

| Extension | Found Files | Total Size | Notes |
| :--- | :---: | :---: | :--- |
| `*.gguf` | 0 | 0 bytes | No GGUF quantized models exist anywhere. |
| `*.safetensors` | 0 | 0 bytes | No HuggingFace/Safetensors weights exist. |
| `*.pt` / `*.pth` | 0 | 0 bytes | No PyTorch checkpoints exist. |
| `*.onnx` | 0 | 0 bytes | No ONNX execution graphs exist. |
| `*.mlx` | 0 | 0 bytes | No Apple MLX models exist. |
| `*.bin` | 3 | 176 bytes | Plain text candidate dummy files (see below). |

### Content of Discovered `*.bin` Files:
1. `D:\AURArainsura-local-v1-candidate-5ee064\model.bin`:
   - Size: **53 bytes**
   - SHA-256: `90b144fa5ee2298fb0299f2e7c3b28b6d210543e0d866a2f815ea7fa780d603e`
   - Content: `AURA_BRAIN_CANDIDATE_aura-local-v1-candidate-5ee064_1`
2. `D:\AURArainsura-local-v1-candidate-fdf48c\model.bin`:
   - Size: **53 bytes**
   - SHA-256: `40be6d10c2826cf6f2641cb9dbec94bbca805eec0685ba05b22596be1aaee907`
   - Content: `AURA_BRAIN_CANDIDATE_aura-local-v1-candidate-fdf48c_1`
3. `D:\AURArainsura-local-v1-candidate-fdf48c-candidate-e9d460\model.bin`:
   - Size: **70 bytes**
   - SHA-256: `785e09f583eb721ecb0e8b1b51e44ea8eb6610058e578c772be47b3105553e1a`
   - Content: `AURA_BRAIN_CANDIDATE_aura-local-v1-candidate-fdf48c-candidate-e9d460_1`

### Manifest vs. Physical Reality Discrepancy
In `D:\AURArainsura-local-v1\manifest.json`:
```json
{
  "brain_id": "aura-local-v1",
  "version": "1.0.0",
  "model_format": "deterministic",
  "context_length": 4096,
  "parameter_count": "7B",
  "quantization": "Q4_K_M",
  "runtime_backend": "cuda",
  "status": "ACTIVE"
}
```
**Forensic Discrepancy:** The manifest claims `"parameter_count": "7B"`, `"quantization": "Q4_K_M"`, and `"runtime_backend": "cuda"`, yet:
1. There is no weights file inside `brains/aura-local-v1/`.
2. The `model_format` is declared as `"deterministic"`.
3. CUDA is never initialized.
The 7B Q4_K_M declaration is purely descriptive metadata that has no physical counterpart in the repository.

---

## 5. Local Port & Inference Server Audit

A socket connection probe was executed against standard local inference ports on `127.0.0.1`:

| Port | Service Tested | Socket Status | Result |
| :---: | :--- | :---: | :--- |
| **11434** | Ollama HTTP API | `ConnectionRefusedError` | No daemon running |
| **8000** | vLLM / FastChat | `ConnectionRefusedError` | No server running |
| **8080** | llama.cpp server | `ConnectionRefusedError` | No server running |
| **5000** | Text Generation WebUI | `ConnectionRefusedError` | No server running |
| **1234** | LM Studio API | `ConnectionRefusedError` | No server running |

**Conclusion:** AURA does not communicate with any background inference daemon on the host machine.

---

## 6. Analysis of `HttpInferenceBackend`: Dead Code Verification

`brain/local_runtime.py` defines `HttpInferenceBackend` (lines 187–315), which sends OpenAI-compatible JSON requests to `http://127.0.0.1:11434/v1/chat/completions`.

### Is it wired into production?
**NO.**
A repository-wide symbol search reveals:
1. `HttpInferenceBackend` is defined in `brain/local_runtime.py`.
2. `HttpInferenceBackend` is imported and tested in `tests/test_aura_local_ai.py` (with a mock HTTP server).
3. `HttpInferenceBackend` is tested in `scripts/verify_aura_local_ai_live.py` (with a dummy test server).
4. **ZERO production modules instantiate `HttpInferenceBackend`.**
5. `LocalAuraBrain` defaults to `LocalModelRuntime()`, which defaults to `DeterministicBackend`.

Even if a developer manually passes `backend=HttpInferenceBackend()` to `LocalAuraBrain`, it connects to Ollama via HTTP. Therefore, it would be an **external daemon wrapper**, not native inference inside AURA.

---

## 7. Tool Protocol Understanding: Intrinsic vs. Hardcoded

Does `LocalAuraBrain` understand tool schemas and arguments?
In `brain/local_runtime.py` lines 156–175:
```python
# Check configured tool rules
for pattern, tool_name, default_args in self._custom_tool_rules:
    if pattern.search(clean_user) and tool_name in available_tools:
        req = ToolCallRequest(call_id=new_tool_call_id(), name=tool_name, arguments=default_args)
        return ModelTurn(tool_calls=(req,))

# Built-in deterministic tool recognition for canonical tools
if "calculator" in clean_user or "máy tính" in clean_user:
    if "android.launch_app" in available_tools:
        req = ToolCallRequest(
            call_id=new_tool_call_id(),
            name="android.launch_app",
            arguments={"package": "com.android.calculator2", "activity": ""},
        )
        return ModelTurn(tool_calls=(req,))
```

**Forensic Finding:** Tool selection in `LocalAuraBrain` is completely deterministic:
- If the word `"calculator"` or `"máy tính"` is present, it returns `android.launch_app` with package `com.android.calculator2`.
- If `"screenshot"` or `"chụp màn hình"` is present, it returns `android.screenshot`.
- It does not read, parse, or reason over tool schemas. It matches hardcoded substrings and returns static dictionary structures.

---

## 8. Compound Task Planner Failure with Local Brain

When `CompoundTaskPlanner.plan_from_goal(goal, llm)` is called with `LocalAuraBrain`, the planner crashes:
```
ValueError: Planning failed: plan rejected after 2 repair attempts.
Last error: JSON parsing failed: Failed to parse plan JSON from model response
```
### Root Cause:
`CompoundTaskPlanner` prompts the LLM to output a complex JSON structure:
`{"goal": "...", "steps": [{"step_id": 1, "tool_name": "...", "args": {...}}]}`
However, `DeterministicBackend` generates the string:
`"Aura local brain response to: Open settings and check battery"`
Because `DeterministicBackend` cannot produce structured JSON, multi-step agent planning is **impossible with the current Local Brain**. Multi-step planning functions **only when connected to a cloud model (Gemini)**.

---

## 9. Performance & Latency Reality

Previous reports claimed sub-millisecond local inference.
### Forensic Reality:
- Model Load Time: **0.0001 seconds** (because loading simply sets an object reference and reads no file).
- First Token Latency: **0.0002 seconds** (instantaneous Python string lookup).
- Tokens per Second: **N/A** (no tokens generated; streaming is simulated by splitting a pre-existing string on spaces and sleeping 10ms per word).
- GPU Usage: **0.0 MB** (CUDA is never called; torch is never imported).
- RAM Usage: **< 1 MB** (pure Python text storage).

These metrics do not reflect high-performance neural acceleration; they reflect Python dictionary lookups.

---

## 10. Clean-Environment Boot Behavior

When AURA boots on a clean system without internet, without API keys, and with default configuration:
1. `server/runtime.py` initializes `BrainRouter(self.config)`.
2. `config.yaml` specifies `llm.provider: gemini` and fallbacks `[groq, mistral, openrouter]`.
3. `BrainRouter` attempts to initialize Gemini: `GEMINI_API_KEY` is missing.
4. `BrainRouter` attempts fallbacks: `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY` are all missing.
5. `BrainRouter` raises:
   `ValueError: Primary provider gemini could not be initialized (GEMINI_API_KEY is not set) and no fallback provider was available`
6. **The server crashes immediately on startup.**

AURA only starts offline if the user explicitly exports `AURA_OFFLINE=1` in their environment before starting the server. When `AURA_OFFLINE=1` is set, `BrainRouter` bypasses the cloud chain and selects `local_aura`, which runs `DeterministicBackend`.
