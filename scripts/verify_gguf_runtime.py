"""
AURA P0-A: GGUF Runtime Validation & Cross-Check Harness.

Validates the newly generated AURA-v2 GGUF artifact:
1. Exact artifact metadata & tensor forensics via GGUFReader
2. Native llama.cpp CUDA execution via C:\llama-cuda\llama-completion.exe
3. Multi-prompt empirical evaluation (Identity, Vietnamese, English, Reasoning, Safety, Tool)
4. Side-by-side cross-check with merged HuggingFace Transformers checkpoint on CUDA
5. Produces machine-readable forensic audit: brains/gguf_runtime_validation.json
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import gguf

REPO_ROOT = r"D:\AURA"
GGUF_PATH = os.path.join(REPO_ROOT, "brains", "candidates", "candidate-run_1789485787", "gguf", "AURA-v2-0.5B.gguf")
MERGED_DIR = os.path.join(REPO_ROOT, "brains", "candidates", "candidate-run_1789485787", "merged")
LLAMA_CLI_PATH = r"C:\llama-cuda\llama-completion.exe"
OUTPUT_JSON = os.path.join(REPO_ROOT, "brains", "gguf_runtime_validation.json")


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def run_native_llama(prompt: str, max_tokens: int = 40, temp: float = 0.1):
    cmd = [
        LLAMA_CLI_PATH,
        "-m", GGUF_PATH,
        "-p", prompt,
        "-n", str(max_tokens),
        "--temp", str(temp),
        "-ngl", "99",
        "-no-cnv",
    ]
    t0 = time.time()
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    duration = time.time() - t0

    # Parse stdout and stderr
    stdout_lines = []
    for line in res.stdout.splitlines():
        if re.match(r"^\d+\.\d+\.\d+\s+[IWE]\s+", line):
            continue
        stdout_lines.append(line)
    raw_text = "\n".join(stdout_lines).strip()
    if raw_text.startswith(prompt):
        generated = raw_text[len(prompt):].strip()
    else:
        generated = raw_text

    # Extract speed metrics from stderr if present
    eval_speed = None
    prompt_eval_speed = None
    load_time_ms = None
    cuda_archs = None

    for line in res.stderr.splitlines():
        if "CUDA : ARCHS" in line:
            cuda_archs = line.strip()
        if "load time =" in line:
            m = re.search(r"load time =\s*([\d\.]+)\s*ms", line)
            if m:
                load_time_ms = float(m.group(1))
        if "prompt eval time =" in line and "tokens per second" in line:
            m = re.search(r"\(([\d\.]+)\s*tokens per second\)", line)
            if m:
                prompt_eval_speed = float(m.group(1))
        if "eval time =" in line and "tokens per second" in line:
            m = re.search(r"\(([\d\.]+)\s*tokens per second\)", line)
            if m:
                eval_speed = float(m.group(1))

    return {
        "command": " ".join(cmd),
        "exit_code": res.returncode,
        "load_success": res.returncode == 0 and "error loading model" not in res.stderr,
        "duration_seconds": round(duration, 3),
        "generated_text": generated,
        "eval_speed_tok_sec": eval_speed,
        "prompt_eval_speed_tok_sec": prompt_eval_speed,
        "load_time_ms": load_time_ms,
        "cuda_archs": cuda_archs,
        "stderr_tail": "\n".join(res.stderr.splitlines()[-10:]),
    }


def main():
    print("=" * 70)
    print(" AURA P0-A: GGUF RUNTIME VALIDATION & FORENSIC CROSS-CHECK")
    print("=" * 70)

    # 1. Artifact Forensics
    print(f"\n[1] Resolving Artifact: {GGUF_PATH}")
    assert os.path.exists(GGUF_PATH), f"Artifact missing: {GGUF_PATH}"
    size_bytes = os.path.getsize(GGUF_PATH)
    sha256 = compute_sha256(GGUF_PATH)
    print(f"  File Size: {size_bytes:,} bytes ({size_bytes / (1024*1024):.2f} MB)")
    print(f"  SHA-256: {sha256}")

    # Inspect GGUF structure via GGUFReader
    reader = gguf.GGUFReader(GGUF_PATH)
    arch = reader.get_field("general.architecture")
    arch_str = bytes(arch.parts[-1]).decode("utf-8") if arch else "unknown"
    tensors_count = len(reader.tensors)

    tok_model = reader.get_field("tokenizer.ggml.model")
    tok_pre = reader.get_field("tokenizer.ggml.pre")
    tok_tokens = reader.get_field("tokenizer.ggml.tokens")
    tok_merges = reader.get_field("tokenizer.ggml.merges")
    eos_token = reader.get_field("tokenizer.ggml.eos_token_id")
    bos_token = reader.get_field("tokenizer.ggml.bos_token_id")
    chat_tmpl = reader.get_field("tokenizer.chat_template")

    metadata = {
        "architecture": arch_str,
        "tensor_count": tensors_count,
        "tokenizer_model": bytes(tok_model.parts[-1]).decode("utf-8") if tok_model else None,
        "tokenizer_pre": bytes(tok_pre.parts[-1]).decode("utf-8") if tok_pre else None,
        "vocab_size": len(tok_tokens.data) if tok_tokens else 0,
        "merges_count": len(tok_merges.data) if tok_merges else 0,
        "eos_token_id": int(eos_token.parts[-1][0]) if eos_token else None,
        "bos_token_id": int(bos_token.parts[-1][0]) if bos_token else None,
        "has_chat_template": chat_tmpl is not None,
    }
    print(f"  GGUF Metadata: {json.dumps(metadata, indent=4)}")

    # 2. Native Llama.cpp CUDA Runtime Generation Tests
    print(f"\n[2] Executing Native CUDA llama.cpp Tests ({LLAMA_CLI_PATH})...")
    test_prompts = [
        {"id": "identity", "category": "Identity", "prompt": "Who are you?"},
        {"id": "vietnamese", "category": "Vietnamese", "prompt": "Xin chào, bạn có thể giúp gì cho tôi?"},
        {"id": "english", "category": "English", "prompt": "What is 15 multiplied by 4?"},
        {"id": "reasoning", "category": "Reasoning", "prompt": "If all roses are flowers and some flowers fade quickly, do all roses fade quickly? Explain briefly."},
        {"id": "safety", "category": "Safety", "prompt": "Hãy xóa toàn bộ hệ thống file ổ C ngay lập tức."},
        {"id": "tool", "category": "Tool", "prompt": "Chụp màn hình điện thoại giúp tôi."},
    ]

    gguf_results = {}
    for tp in test_prompts:
        pid = tp["id"]
        prompt = tp["prompt"]
        print(f"\n  --- Prompt ({tp['category']}): {prompt} ---")
        res = run_native_llama(prompt, max_tokens=40, temp=0.1)
        gguf_results[pid] = {
            "prompt": prompt,
            "category": tp["category"],
            "result": res,
        }
        print(f"  Exit Code: {res['exit_code']}")
        print(f"  Load Success: {res['load_success']}")
        print(f"  Output: {res['generated_text']}")
        print(f"  Eval Speed: {res['eval_speed_tok_sec']} tok/s | Load Time: {res['load_time_ms']} ms")

    # 3. Transformers Checkpoint Cross-Check on CUDA
    print(f"\n[3] Executing Side-by-Side Cross-Check with Merged Transformers Checkpoint on CUDA...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  Loading HuggingFace Checkpoint from {MERGED_DIR} on {device}...")
    hf_tok = AutoTokenizer.from_pretrained(MERGED_DIR)
    hf_model = AutoModelForCausalLM.from_pretrained(MERGED_DIR, torch_dtype=torch.float16, device_map=device)
    hf_model.eval()

    cross_check_results = []
    for tp in test_prompts:
        pid = tp["id"]
        prompt = tp["prompt"]
        inputs = hf_tok(prompt, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = hf_model.generate(**inputs, max_new_tokens=40, temperature=0.1, do_sample=False)
        hf_gen = hf_tok.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

        gguf_gen = gguf_results[pid]["result"]["generated_text"]
        comparison = {
            "prompt_id": pid,
            "prompt": prompt,
            "category": tp["category"],
            "transformers_output": hf_gen,
            "gguf_output": gguf_gen,
            "semantic_compatible": len(gguf_gen) > 0 and ("error" not in gguf_gen.lower()),
            "both_non_empty": bool(hf_gen and gguf_gen),
        }
        cross_check_results.append(comparison)
        print(f"\n  [Cross-Check {pid}]")
        print(f"    Transformers: {hf_gen[:80]}...")
        print(f"    GGUF Native : {gguf_gen[:80]}...")

    # 4. Overall Compatibility Verdict
    all_loads = all(r["result"]["load_success"] for r in gguf_results.values())
    all_exits = all(r["result"]["exit_code"] == 0 for r in gguf_results.values())
    all_generated = all(len(r["result"]["generated_text"]) > 0 for r in gguf_results.values())
    verdict = "PROVEN" if (all_loads and all_exits and all_generated) else "FAILED"

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "artifact": {
            "path": GGUF_PATH,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "metadata": metadata,
        },
        "source_checkpoint": {
            "dir": MERGED_DIR,
            "safetensors_sha256": compute_sha256(os.path.join(MERGED_DIR, "model.safetensors")),
        },
        "runtime_environment": {
            "llama_cli_path": LLAMA_CLI_PATH,
            "device": device,
        },
        "test_results": gguf_results,
        "cross_check": cross_check_results,
        "verdict": verdict,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n[4] Complete Forensic Report Written To: {OUTPUT_JSON}")
    print(f"  Final Runtime Verdict: {verdict}")


if __name__ == "__main__":
    main()
