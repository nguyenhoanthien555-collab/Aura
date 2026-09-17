"""
AURA Real Neural Brain LoRA / QLoRA Trainer.

Performs genuine parameter adaptation on open-weight base models using PEFT LoRA.
Executes real forward passes, real cross-entropy loss computation, real backpropagation,
real AdamW optimizer parameter updates, and saves authentic adapter artifacts.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model, TaskType, PeftModel

from core.logger import logger
from learning.parameter_tracker import ParameterTracker
from learning.lineage import resolve_base_model_info, ModelLineage


@dataclass
class TrainingResult:
    job_id: str
    base_model: str
    base_model_hash: str
    dataset_path: str
    dataset_hash: str
    adapter_dir: str
    adapter_sha256: str
    trainable_parameters: int
    total_parameters: int
    trainable_percentage: float
    initial_loss: float
    final_loss: float
    loss_history: List[float]
    epochs_completed: int
    steps_completed: int
    duration_seconds: float
    peak_vram_mb: float
    device: str
    created_at: str
    parameter_deltas: Dict[str, Any] = field(default_factory=dict)
    lineage: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class AuraNeuralTrainer:
    """
    Autonomous On-Device Neural Trainer for AURA.
    Trains LoRA adapters on local GPU (RTX 4060) without external cloud dependencies.
    """

    def __init__(
        self,
        base_model_id: str = "Qwen/Qwen2.5-0.5B-Instruct",
        device: Optional[str] = None,
        torch_dtype: torch.dtype = torch.float16,
    ):
        self.base_model_id = base_model_id
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        self.torch_dtype = torch_dtype

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def load_dataset_samples(self, dataset_path: str, tokenizer) -> List[Dict[str, torch.Tensor]]:
        """
        Parses JSONL training dataset and prepares tokenized input_ids and labels.
        Supports standard AURA messages format: [{'role': 'user'|'assistant'|'system', 'content': '...'}]
        """
        samples = []
        with open(dataset_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                messages = record.get("messages", [])
                if not messages:
                    if "prompt" in record and "response" in record:
                        messages = [
                            {"role": "user", "content": record["prompt"]},
                            {"role": "assistant", "content": record["response"]}
                        ]
                    elif "input" in record and "output" in record:
                        messages = [
                            {"role": "user", "content": record["input"]},
                            {"role": "assistant", "content": record["output"]}
                        ]

                if not messages:
                    continue

                try:
                    formatted_text = tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=False,
                    )
                except Exception:
                    formatted_text = ""
                    for m in messages:
                        r = m.get("role", "user")
                        c = m.get("content", "")
                        formatted_text += f"<|im_start|>{r}\n{c}<|im_end|>\n"

                tokens = tokenizer(
                    formatted_text,
                    truncation=True,
                    max_length=1024,
                    return_tensors="pt"
                )
                input_ids = tokens["input_ids"].squeeze(0)
                attention_mask = tokens["attention_mask"].squeeze(0)
                labels = self._mask_labels_to_assistant(
                    input_ids, tokenizer, messages
                )

                samples.append({
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "labels": labels,
                })
        return samples

    @staticmethod
    def _mask_labels_to_assistant(
        input_ids: torch.Tensor, tokenizer, messages: List[Dict[str, Any]]
    ) -> torch.Tensor:
        """
        Loss-masking for supervised fine-tuning: only assistant tokens
        contribute to the loss. System and user tokens are set to -100 so
        the model is not trained to predict its own prompts (the P0/P1
        bottleneck: full-sequence labels diluted the learning signal and
        encouraged prompt memorisation).
        """
        labels = input_ids.clone()

        # Find where the assistant turn begins. The chat template renders
        # each assistant turn starting with the "<|im_start|>assistant\n"
        # header; we keep loss on that header's continuation and the
        # response itself.
        im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
        assistant_header_ids = tokenizer(
            "<|im_start|>assistant\n", add_special_tokens=False
        )["input_ids"]
        header_len = len(assistant_header_ids)

        header_positions: List[int] = []
        if header_len > 0:
            ids = input_ids.tolist()
            first = assistant_header_ids[0]
            for i in range(len(ids) - header_len + 1):
                if ids[i] == first and ids[i:i + header_len] == assistant_header_ids:
                    header_positions.append(i)

        if not header_positions:
            # Header not found (unexpected template); fall back to masking
            # nothing rather than corrupting training silently.
            return labels

        # Mask everything before the first assistant generated token
        # (including the <|im_start|>assistant\n header itself).
        first_content_start = header_positions[0] + header_len
        labels[:first_content_start] = -100

        # Handle each assistant turn: keep assistant tokens through <|im_end|>,
        # and mask user/system turns or padding in between.
        ids_len = len(input_ids)
        for idx, start in enumerate(header_positions):
            content_start = start + header_len
            end_pos = ids_len
            for j in range(content_start, ids_len):
                if input_ids[j].item() == im_end_id:
                    end_pos = j  # Keep <|im_end|> unmasked so the model learns when to stop
                    break

            if idx + 1 < len(header_positions):
                next_content_start = header_positions[idx + 1] + header_len
                labels[end_pos + 1:next_content_start] = -100
            else:
                labels[end_pos + 1:] = -100

        return labels

    def train(
        self,
        dataset_path: str,
        output_dir: str,
        job_id: str = "job_001",
        epochs: int = 1,
        max_steps: Optional[int] = None,
        learning_rate: float = 2e-4,
        lora_r: int = 8,
        lora_alpha: int = 16,
        lora_dropout: float = 0.05,
    ) -> TrainingResult:
        """
        Executes a genuine neural training session on the local device.
        """
        os.makedirs(output_dir, exist_ok=True)
        dataset_hash = self.compute_file_hash(dataset_path)

        logger.info(
            "[AuraNeuralTrainer] Initializing training job %s on %s with base %s",
            job_id, self.device, self.base_model_id
        )

        start_time = time.time()
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()

        # 1. Load Tokenizer & Base Model
        tokenizer = AutoTokenizer.from_pretrained(self.base_model_id)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        base_model = AutoModelForCausalLM.from_pretrained(
            self.base_model_id,
            dtype=self.torch_dtype,
            device_map=self.device,
        )

        # 2. Attach PEFT LoRA Adapter
        peft_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=lora_r,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        )
        model = get_peft_model(base_model, peft_config)
        trainable_params, all_params = model.get_nb_trainable_parameters()
        trainable_pct = 100 * trainable_params / all_params

        logger.info(
            "[AuraNeuralTrainer] LoRA attached: %d / %d params trainable (%.3f%%)",
            trainable_params, all_params, trainable_pct
        )

        # 3. Prepare Dataset
        samples = self.load_dataset_samples(dataset_path, tokenizer)
        if not samples:
            raise ValueError(f"No valid training samples parsed from {dataset_path}")

        logger.info("[AuraNeuralTrainer] Loaded %d training examples", len(samples))

        # 4. Set Up Optimizer & Capture Pre-Training Snapshot
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        model.train()
        snapshot_before = ParameterTracker.capture_snapshot(model, only_trainable=True)

        # 5. Training Loop
        loss_history = []
        global_step = 0
        initial_loss = 0.0

        for epoch in range(epochs):
            for sample in samples:
                input_ids = sample["input_ids"].unsqueeze(0).to(self.device)
                attention_mask = sample["attention_mask"].unsqueeze(0).to(self.device)
                labels = sample["labels"].unsqueeze(0).to(self.device)

                optimizer.zero_grad()
                outputs = model(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    labels=labels
                )
                loss = outputs.loss
                current_loss = float(loss.item())

                if global_step == 0:
                    initial_loss = current_loss

                loss_history.append(current_loss)
                loss.backward()

                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()

                global_step += 1
                if global_step % 5 == 0 or global_step == 1:
                    logger.info(
                        "[AuraNeuralTrainer] Step %d | Epoch %d/%d | Loss: %.4f",
                        global_step, epoch + 1, epochs, current_loss
                    )

                if max_steps is not None and global_step >= max_steps:
                    break

            if max_steps is not None and global_step >= max_steps:
                break

        final_loss = loss_history[-1] if loss_history else initial_loss
        duration = time.time() - start_time

        # Capture Post-Training Snapshot & Compute Exact Parameter Delta
        snapshot_after = ParameterTracker.capture_snapshot(model, only_trainable=True)
        delta_report = ParameterTracker.compute_delta(snapshot_before, snapshot_after)
        delta_report_path = os.path.join(output_dir, "parameter_delta.json")
        delta_report.save(delta_report_path)

        logger.info(
            "[AuraNeuralTrainer] Parameter Delta: %d / %d params changed (%.3f%%), max abs delta: %.6f, L2 norm: %.6f",
            delta_report.changed_parameters_count,
            delta_report.total_parameters_tracked,
            delta_report.changed_parameters_pct,
            delta_report.max_abs_delta,
            delta_report.l2_norm_delta,
        )

        peak_vram = 0.0
        if self.device == "cuda":
            peak_vram = torch.cuda.max_memory_allocated() / (1024 * 1024)

        # 6. Save Adapter Artifacts
        adapter_save_dir = os.path.join(output_dir, "adapter")
        model.save_pretrained(adapter_save_dir)
        tokenizer.save_pretrained(adapter_save_dir)

        # 7. Compute Hash of Trained Adapter Weights
        adapter_weight_path = os.path.join(adapter_save_dir, "adapter_model.safetensors")
        if not os.path.exists(adapter_weight_path):
            adapter_weight_path = os.path.join(adapter_save_dir, "adapter_model.bin")

        adapter_hash = ""
        if os.path.exists(adapter_weight_path):
            adapter_hash = self.compute_file_hash(adapter_weight_path)

        # 8. Resolve Real Base Model Lineage and Save lineage.json
        try:
            base_info = resolve_base_model_info(self.base_model_id)
            base_model_hash = base_info["primary_artifact_hash"]
        except Exception as e:
            logger.warning("[AuraNeuralTrainer] Fallback lineage resolution: %s", e)
            base_info = {"format": "safetensors", "files": [], "total_bytes": 0, "parameter_count": all_params}
            base_model_hash = "sha256_unresolved"

        config_hash = hashlib.sha256(
            json.dumps({"lr": learning_rate, "r": lora_r, "alpha": lora_alpha, "steps": global_step}, sort_keys=True).encode()
        ).hexdigest()

        lineage = ModelLineage(
            model_id=job_id,
            parent_model_id=self.base_model_id,
            parent_artifact_hash=base_model_hash,
            base_model_format=base_info.get("format", "safetensors"),
            base_model_files=base_info.get("files", []),
            base_model_total_bytes=base_info.get("total_bytes", 0),
            base_model_parameter_count=base_info.get("parameter_count", all_params),
            training_dataset_hash=dataset_hash,
            training_config_hash=config_hash,
            adapter_hash=adapter_hash,
            metadata={
                "steps": global_step,
                "initial_loss": initial_loss,
                "final_loss": final_loss,
                "loss_delta": round(initial_loss - final_loss, 4),
                "peak_vram_mb": round(peak_vram, 2),
            },
        )
        lineage.save(os.path.join(output_dir, "lineage.json"))

        # Save training run metadata
        res = TrainingResult(
            job_id=job_id,
            base_model=self.base_model_id,
            base_model_hash=base_model_hash,
            dataset_path=dataset_path,
            dataset_hash=dataset_hash,
            adapter_dir=adapter_save_dir,
            adapter_sha256=adapter_hash,
            trainable_parameters=trainable_params,
            total_parameters=all_params,
            trainable_percentage=trainable_pct,
            initial_loss=initial_loss,
            final_loss=final_loss,
            loss_history=loss_history,
            epochs_completed=epoch + 1,
            steps_completed=global_step,
            duration_seconds=round(duration, 3),
            peak_vram_mb=round(peak_vram, 2),
            device=self.device,
            created_at=datetime.now().isoformat(),
            parameter_deltas=delta_report.to_dict(),
            lineage=lineage.to_dict(),
            metrics={
                "loss_delta": round(initial_loss - final_loss, 4),
                "steps": global_step,
                "examples": len(samples),
            }
        )

        meta_path = os.path.join(output_dir, "training_result.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(res.to_dict(), f, indent=2)

        logger.info(
            "[AuraNeuralTrainer] Training completed in %.2fs. Steps: %d, Loss: %.4f -> %.4f (VRAM: %.1f MB). Adapter SHA: %s",
            duration, global_step, initial_loss, final_loss, peak_vram, adapter_hash[:12]
        )

        return res
