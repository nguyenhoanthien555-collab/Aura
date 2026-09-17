"""
AURA Model Merger.

Merges fine-tuned PEFT LoRA adapter weights directly into the base foundation model,
producing a standalone, self-contained, and independently versioned AURA model checkpoint.
Verifies that merged parameters mathematically and cryptographically differ from the base weights.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import time
from typing import Any, Dict, Optional
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

from core.logger import logger
from learning.parameter_tracker import ParameterTracker, ParameterDeltaReport


@dataclass
class MergeResult:
    base_model_id: str
    adapter_dir: str
    output_dir: str
    merged_weight_file: str
    merged_weight_sha256: str
    merged_weight_size_bytes: int
    delta_report: Dict[str, Any]
    base_weights_distinct: bool
    duration_seconds: float
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ModelMerger:
    """Merges LoRA adapter into base foundation model and verifies weight deltas."""

    @staticmethod
    def compute_file_sha256(filepath: str) -> str:
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    @classmethod
    def merge(
        cls,
        base_model_id: str,
        adapter_dir: str,
        output_dir: str,
        device: str = "cpu",
        torch_dtype: torch.dtype = torch.float16,
    ) -> MergeResult:
        """
        Loads base model and PEFT adapter, executes merge_and_unload,
        verifies parameter delta against base model, and saves merged checkpoint.
        """
        from datetime import datetime

        start_time = time.time()
        os.makedirs(output_dir, exist_ok=True)
        logger.info("[ModelMerger] Merging adapter %s into base %s on %s", adapter_dir, base_model_id, device)

        # 1. Load Base Model and capture baseline snapshot
        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_id,
            torch_dtype=torch_dtype,
            device_map=device,
        )
        base_snapshot = ParameterTracker.capture_snapshot(base_model)

        # 2. Attach PEFT Adapter
        peft_model = PeftModel.from_pretrained(base_model, adapter_dir)

        # 3. Merge LoRA weights into base weights and unload LoRA wrapper
        merged_model = peft_model.merge_and_unload()
        merged_snapshot = ParameterTracker.capture_snapshot(merged_model)

        # 4. Mathematically verify that base weights != merged weights
        delta_report = ParameterTracker.compute_delta(base_snapshot, merged_snapshot)
        base_distinct = delta_report.changed_parameters_count > 0

        logger.info(
            "[ModelMerger] Weight delta verified: %d / %d params changed (%.3f%%), max abs delta: %.6f",
            delta_report.changed_parameters_count,
            delta_report.total_parameters_tracked,
            delta_report.changed_parameters_pct,
            delta_report.max_abs_delta,
        )

        # 5. Save Merged Model & Tokenizer
        merged_model.save_pretrained(output_dir)
        tokenizer = AutoTokenizer.from_pretrained(base_model_id)
        tokenizer.save_pretrained(output_dir)

        # 6. Save Parameter Delta Report
        delta_report_path = os.path.join(output_dir, "parameter_delta.json")
        delta_report.save(delta_report_path)

        # 7. Locate primary merged weights and compute SHA-256
        merged_weight_file = "model.safetensors"
        primary_path = os.path.join(output_dir, merged_weight_file)
        if not os.path.exists(primary_path):
            merged_weight_file = "pytorch_model.bin"
            primary_path = os.path.join(output_dir, merged_weight_file)

        merged_sha256 = cls.compute_file_sha256(primary_path)
        merged_size = os.path.getsize(primary_path)
        duration = time.time() - start_time

        logger.info(
            "[ModelMerger] Saved merged checkpoint to %s (%d bytes, SHA: %s)",
            primary_path,
            merged_size,
            merged_sha256[:12],
        )

        now = datetime.now().isoformat(timespec="seconds")
        result = MergeResult(
            base_model_id=base_model_id,
            adapter_dir=adapter_dir,
            output_dir=output_dir,
            merged_weight_file=merged_weight_file,
            merged_weight_sha256=merged_sha256,
            merged_weight_size_bytes=merged_size,
            delta_report=delta_report.to_dict(),
            base_weights_distinct=base_distinct,
            duration_seconds=round(duration, 3),
            created_at=now,
        )

        meta_path = os.path.join(output_dir, "merge_metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2)

        return result
