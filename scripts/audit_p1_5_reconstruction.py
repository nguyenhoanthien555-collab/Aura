# -*- coding: utf-8 -*-
"""
AURA P1.5 Forensic Reconstruction & Audit Script.

Reconstructs P1.5-E001 empirical results, audits per-case scores across
all models (Parent Base, Candidate LoRA, Merged Safetensors, Candidate GGUF,
Production 3B), investigates the LoRA vs GGUF parity gap (Q2), inspects loss
masking tokens, and outputs comprehensive machine-readable evidence.
"""

import json
import os
import sys
import time
import hashlib
import re

REPO_ROOT = r"D:\AURA"
sys.path.insert(0, REPO_ROOT)

import torch
from learning.heldout import HELD_OUT_SUITE, heldout_dataset_hash
from learning.quality_eval import (
    EVAL_SEED,
    EVAL_TEMPERATURE,
    EVAL_MAX_TOKENS,
    EVAL_SYSTEM_PROMPT,
    NeuralHarness,
    GGUFHarness,
    _run_case,
    evaluate_suite,
)
from learning.curriculum import get_stable_core_examples
from learning.contamination import check_contamination


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_dataset_and_curriculum():
    ds_path = r"D:\AURA\data\aura\dataset_cycle_1789542202_run_59.jsonl"
    raw_bytes = open(ds_path, "rb").read()
    raw_lf = raw_bytes.replace(b"\r\n", b"\n")
    
    crlf_sha = hashlib.sha256(raw_bytes).hexdigest()
    lf_sha = hashlib.sha256(raw_lf).hexdigest()

    records = []
    with open(ds_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    # Distribution breakdown
    categories = {}
    languages = {"vietnamese": 0, "english": 0}
    tool_vs_text = {"tool_use": 0, "text_response": 0}
    actions_vs_refusals = {"action": 0, "refusal": 0, "clarification": 0, "identity": 0, "reasoning": 0}
    core_count = 0
    experience_count = 0

    for r in records:
        cat = r.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
        
        prov = r.get("provenance", {})
        source = prov.get("source", "")
        if "core" in source.lower() or "curriculum" in source.lower():
            core_count += 1
        else:
            experience_count += 1

        msgs = r.get("messages", [])
        user_text = ""
        asst_text = ""
        has_tool = False
        for m in msgs:
            if m.get("role") == "user":
                user_text = m.get("content", "")
            elif m.get("role") == "assistant":
                asst_text = m.get("content", "")
                if m.get("tool_calls"):
                    has_tool = True

        if has_tool:
            tool_vs_text["tool_use"] += 1
            actions_vs_refusals["action"] += 1
        else:
            tool_vs_text["text_response"] += 1
            low = asst_text.lower()
            if any(w in low for w in ["không thể", "không có khả năng", "cannot", "don't have", "không hỗ trợ"]):
                actions_vs_refusals["refusal"] += 1
            elif cat == "clarification" or "?" in asst_text:
                actions_vs_refusals["clarification"] += 1
            elif cat == "identity" or "aura" in low:
                actions_vs_refusals["identity"] += 1
            else:
                actions_vs_refusals["reasoning"] += 1

        vn_chars = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
        if any(c in user_text.lower() for c in vn_chars):
            languages["vietnamese"] += 1
        else:
            languages["english"] += 1

    contam_report = check_contamination(ds_path)

    return {
        "file": ds_path,
        "total_examples": len(records),
        "experience_examples": experience_count,
        "stable_core_examples": core_count,
        "crlf_sha256": crlf_sha,
        "lf_sha256": lf_sha,
        "categories": categories,
        "languages": languages,
        "tool_vs_text": tool_vs_text,
        "actions_vs_refusals": actions_vs_refusals,
        "contamination": {
            "status": contam_report.contamination_status,
            "overlaps": contam_report.overlap_count,
            "exact": contam_report.exact_overlap_count,
            "near_duplicates": contam_report.near_duplicate_count,
            "prompt_leakage": contam_report.prompt_leakage_count,
        }
    }


def audit_loss_masking_tokens():
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
    
    messages = [
        {"role": "system", "content": "You are AURA, a local-first personal AI companion."},
        {"role": "user", "content": "Hãy bật đèn pin của điện thoại lên giúp tôi."},
        {"role": "assistant", "content": "Tôi không có khả năng điều khiển phần cứng đèn pin trên điện thoại."}
    ]
    formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    tokens = tokenizer(formatted, return_tensors="pt")
    input_ids = tokens["input_ids"].squeeze(0)
    
    from learning.trainer import AuraNeuralTrainer
    labels = AuraNeuralTrainer._mask_labels_to_assistant(input_ids, tokenizer, messages)
    
    token_audit = []
    for idx, (t_id, l_id) in enumerate(zip(input_ids.tolist(), labels.tolist())):
        t_str = tokenizer.decode([t_id])
        token_audit.append({
            "index": idx,
            "token_id": t_id,
            "token_str": t_str,
            "label": l_id,
            "unmasked": l_id != -100
        })
        
    first_unmasked = next(x for x in token_audit if x["unmasked"])
    last_unmasked = list(reversed([x for x in token_audit if x["unmasked"]]))[0]
    
    return {
        "total_tokens": len(token_audit),
        "unmasked_count": sum(1 for x in token_audit if x["unmasked"]),
        "first_unmasked_token": first_unmasked,
        "last_unmasked_token": last_unmasked,
        "header_masked": all(x["label"] == -100 for x in token_audit[:first_unmasked["index"]]),
    }


def run_full_model_evaluations():
    print("Beginning 26-case evaluation across 4 models...")
    models_to_eval = [
        ("parent_base", r"Qwen/Qwen2.5-0.5B-Instruct", None, "torch"),
        ("candidate_adapter", r"Qwen/Qwen2.5-0.5B-Instruct", r"D:\AURA\brains\candidates\candidate-cycle_1789542202_run_59\adapter", "torch"),
        ("candidate_gguf", r"D:\AURA\brains\candidates\candidate-cycle_1789542202_run_59\gguf\AURA-cand-run_59.gguf", None, "gguf"),
        ("production_3b", r"D:\AURA\brains\aura-brain-v1\model.gguf", None, "gguf"),
    ]
    
    results = {}
    
    # 1. GGUF Models
    for name, path, _, kind in models_to_eval:
        if kind != "gguf":
            continue
        print(f"Evaluating {name} ({path})...")
        harness = GGUFHarness(gguf_path=path)
        harness.verify()
        per_case = []
        for case in HELD_OUT_SUITE:
            score, passed, reason, text = _run_case(harness, case)
            per_case.append({
                "test_id": case.test_id,
                "category": case.category,
                "input_text": case.input_text,
                "raw_reply": text,
                "score": score,
                "passed": passed,
                "reason": reason,
            })
        overall = sum(c["score"] for c in per_case) / len(per_case)
        passed_cnt = sum(1 for c in per_case if c["passed"])
        results[name] = {
            "overall_score": overall,
            "passed_count": passed_cnt,
            "total_cases": len(per_case),
            "per_case": per_case,
        }
        print(f"  {name}: {overall:.4f} ({passed_cnt}/26)")
        
    # 2. PyTorch Models (Base & LoRA)
    neural = NeuralHarness(base_model_id="Qwen/Qwen2.5-0.5B-Instruct")
    try:
        # Base
        print("Evaluating parent_base...")
        base_cases = []
        for case in HELD_OUT_SUITE:
            score, passed, reason, text = _run_case(neural, case)
            base_cases.append({
                "test_id": case.test_id,
                "category": case.category,
                "input_text": case.input_text,
                "raw_reply": text,
                "score": score,
                "passed": passed,
                "reason": reason,
            })
        b_overall = sum(c["score"] for c in base_cases) / len(base_cases)
        b_passed = sum(1 for c in base_cases if c["passed"])
        results["parent_base"] = {
            "overall_score": b_overall,
            "passed_count": b_passed,
            "total_cases": len(base_cases),
            "per_case": base_cases,
        }
        print(f"  parent_base: {b_overall:.4f} ({b_passed}/26)")

        # Candidate LoRA
        print("Evaluating candidate_adapter...")
        adapter_dir = r"D:\AURA\brains\candidates\candidate-cycle_1789542202_run_59\adapter"
        adapter_sha = sha256_file(os.path.join(adapter_dir, "adapter_model.safetensors"))
        neural.attach_adapter(adapter_dir, expected_sha256=adapter_sha)
        lora_cases = []
        for case in HELD_OUT_SUITE:
            score, passed, reason, text = _run_case(neural, case)
            lora_cases.append({
                "test_id": case.test_id,
                "category": case.category,
                "input_text": case.input_text,
                "raw_reply": text,
                "score": score,
                "passed": passed,
                "reason": reason,
            })
        l_overall = sum(c["score"] for c in lora_cases) / len(lora_cases)
        l_passed = sum(1 for c in lora_cases if c["passed"])
        results["candidate_adapter"] = {
            "overall_score": l_overall,
            "passed_count": l_passed,
            "total_cases": len(lora_cases),
            "per_case": lora_cases,
        }
        print(f"  candidate_adapter: {l_overall:.4f} ({l_passed}/26)")
    finally:
        neural.unload()

    return results


def analyze_diff(eval_results):
    base_map = {c["test_id"]: c for c in eval_results["parent_base"]["per_case"]}
    lora_map = {c["test_id"]: c for c in eval_results["candidate_adapter"]["per_case"]}
    gguf_map = {c["test_id"]: c for c in eval_results["candidate_gguf"]["per_case"]}
    prod_map = {c["test_id"]: c for c in eval_results["production_3b"]["per_case"]}

    diffs = []
    parity_divergences = []

    for case in HELD_OUT_SUITE:
        cid = case.test_id
        b = base_map[cid]
        l = lora_map[cid]
        g = gguf_map[cid]
        p = prod_map[cid]

        # Classification vs Base for GGUF
        if not b["passed"] and g["passed"]:
            cls_status = "NEW_GAIN"
        elif b["passed"] and not g["passed"]:
            cls_status = "REGRESSION"
        elif b["passed"] and g["passed"]:
            cls_status = "BOTH_PASS"
        else:
            cls_status = "BOTH_FAIL"

        # LoRA vs GGUF Parity Check
        parity_match = (l["passed"] == g["passed"])
        if not parity_match:
            parity_divergences.append({
                "test_id": cid,
                "category": case.category,
                "input_text": case.input_text,
                "lora_passed": l["passed"],
                "lora_score": l["score"],
                "lora_reply": l["raw_reply"][:150],
                "gguf_passed": g["passed"],
                "gguf_score": g["score"],
                "gguf_reply": g["raw_reply"][:150],
            })

        diffs.append({
            "test_id": cid,
            "category": case.category,
            "classification": cls_status,
            "base_score": b["score"],
            "lora_score": l["score"],
            "gguf_score": g["score"],
            "prod_score": p["score"],
            "base_passed": b["passed"],
            "lora_passed": l["passed"],
            "gguf_passed": g["passed"],
            "prod_passed": p["passed"],
            "reason_gguf": g["reason"],
            "raw_gguf_reply": g["raw_reply"],
        })

    return diffs, parity_divergences


def main():
    print("=" * 70)
    print("AURA P1.5 FORENSIC RECONSTRUCTION & RE-AUDIT")
    print("=" * 70)

    # 1. Dataset & Curriculum Audit
    ds_audit = audit_dataset_and_curriculum()
    print("\n[1] Dataset & Curriculum Distribution:")
    print(f"    Total: {ds_audit['total_examples']} (Experiences: {ds_audit['experience_examples']}, Core: {ds_audit['stable_core_examples']})")
    print(f"    Categories: {ds_audit['categories']}")
    print(f"    Languages: {ds_audit['languages']}")
    print(f"    Tool vs Text: {ds_audit['tool_vs_text']}")
    print(f"    Actions vs Refusals: {ds_audit['actions_vs_refusals']}")
    print(f"    CRLF SHA: {ds_audit['crlf_sha256']}")
    print(f"    LF SHA:   {ds_audit['lf_sha256']}")
    print(f"    Contamination: {ds_audit['contamination']}")

    # 2. Loss Masking Audit
    mask_audit = audit_loss_masking_tokens()
    print("\n[2] Loss Masking Token Audit:")
    print(f"    Total Tokens: {mask_audit['total_tokens']}, Unmasked: {mask_audit['unmasked_count']}")
    print(f"    Header Masked to -100: {mask_audit['header_masked']}")
    print(f"    First unmasked token: index {mask_audit['first_unmasked_token']['index']} ('{mask_audit['first_unmasked_token']['token_str']}')")
    print(f"    Last unmasked token: index {mask_audit['last_unmasked_token']['index']} ('{mask_audit['last_unmasked_token']['token_str']}')")

    # 3. Model Evaluations
    eval_results = run_full_model_evaluations()

    # 4. Parity & Diff Analysis
    diffs, parity_div = analyze_diff(eval_results)
    print("\n[4] Parity Divergences between LoRA and GGUF:")
    print(f"    Count: {len(parity_div)}")
    for p in parity_div:
        print(f"    - {p['test_id']} ({p['category']}): LoRA={p['lora_score']} vs GGUF={p['gguf_score']}")
        print(f"      LoRA: {p['lora_reply']}")
        print(f"      GGUF: {p['gguf_reply']}")

    # 5. Compile Evidence JSON
    evidence = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "benchmark_hash": heldout_dataset_hash(),
        "dataset_audit": ds_audit,
        "loss_masking_audit": mask_audit,
        "model_scores": {
            k: {
                "overall_score": v["overall_score"],
                "passed_count": v["passed_count"],
                "total_cases": v["total_cases"],
            } for k, v in eval_results.items()
        },
        "parity_divergences": parity_div,
        "per_case_diff": diffs,
    }

    out_json = r"D:\AURA\artifacts\p1_5_forensic.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {out_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
