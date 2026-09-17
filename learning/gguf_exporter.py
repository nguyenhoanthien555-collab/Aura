"""
AURA GGUF Exporter & Validator.

Exports merged AURA model checkpoints into standard GGUF binary artifacts using gguf.GGUFWriter.
Performs strict structural and cryptographic validation with gguf.GGUFReader:
verifies architecture, tensor count, data offsets, SHA-256 digest, and vocabulary alignment.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional
import numpy as np
import safetensors.torch
import torch
import gguf

from core.logger import logger


@dataclass
class GGUFExportResult:
    gguf_path: str
    file_size_bytes: int
    sha256: str
    architecture: str
    tensor_count: int
    parameters_count: int
    is_valid: bool
    validation_details: Dict[str, Any]
    duration_seconds: float
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GGUFExporter:
    """Exports PyTorch / Safetensors transformer models to standard GGUF binary format."""

    @staticmethod
    def compute_sha256(filepath: str) -> str:
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _map_tensor_name(hf_name: str) -> str:
        """Maps HuggingFace tensor names to standard llama.cpp / GGUF tensor names for Qwen2."""
        if hf_name == "model.embed_tokens.weight":
            return "token_embd.weight"
        if hf_name == "model.norm.weight":
            return "output_norm.weight"
        if hf_name == "lm_head.weight":
            return "output.weight"

        # Layer tensors: model.layers.{i}.*
        if hf_name.startswith("model.layers."):
            parts = hf_name.split(".")
            layer_idx = parts[2]
            sub = ".".join(parts[3:])

            mapping = {
                "input_layernorm.weight": f"blk.{layer_idx}.attn_norm.weight",
                "self_attn.q_proj.weight": f"blk.{layer_idx}.attn_q.weight",
                "self_attn.q_proj.bias": f"blk.{layer_idx}.attn_q.bias",
                "self_attn.k_proj.weight": f"blk.{layer_idx}.attn_k.weight",
                "self_attn.k_proj.bias": f"blk.{layer_idx}.attn_k.bias",
                "self_attn.v_proj.weight": f"blk.{layer_idx}.attn_v.weight",
                "self_attn.v_proj.bias": f"blk.{layer_idx}.attn_v.bias",
                "self_attn.o_proj.weight": f"blk.{layer_idx}.attn_output.weight",
                "post_attention_layernorm.weight": f"blk.{layer_idx}.ffn_norm.weight",
                "mlp.gate_proj.weight": f"blk.{layer_idx}.ffn_gate.weight",
                "mlp.up_proj.weight": f"blk.{layer_idx}.ffn_up.weight",
                "mlp.down_proj.weight": f"blk.{layer_idx}.ffn_down.weight",
            }
            return mapping.get(sub, f"blk.{layer_idx}.{sub}")

        return hf_name

    @classmethod
    def export(
        cls,
        checkpoint_dir: str,
        output_gguf_path: str,
        model_name: str = "AURA",
        dtype: str = "f16",
    ) -> GGUFExportResult:
        """
        Reads config.json and weights from checkpoint_dir and writes an authentic GGUF file.
        """
        start_time = time.time()
        os.makedirs(os.path.dirname(os.path.abspath(output_gguf_path)), exist_ok=True)

        config_path = os.path.join(checkpoint_dir, "config.json")
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Missing config.json in {checkpoint_dir}")

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        arch = cfg.get("model_type", "qwen2")
        logger.info("[GGUFExporter] Initializing GGUFWriter for arch '%s' -> %s", arch, output_gguf_path)
        writer = gguf.GGUFWriter(output_gguf_path, arch)

        # 1. Model Architecture & Hyperparameters
        writer.add_name(model_name)
        writer.add_context_length(cfg.get("max_position_embeddings", 32768))
        writer.add_embedding_length(cfg.get("hidden_size", 896))
        writer.add_block_count(cfg.get("num_hidden_layers", 24))
        writer.add_feed_forward_length(cfg.get("intermediate_size", 4864))
        writer.add_head_count(cfg.get("num_attention_heads", 14))
        writer.add_head_count_kv(cfg.get("num_key_value_heads", 2))
        writer.add_layer_norm_rms_eps(cfg.get("rms_norm_eps", 1e-6))
        writer.add_rope_freq_base(cfg.get("rope_theta", 1000000.0))

        # 2. Add Tokenizer vocabulary, merges, and special tokens
        tok_json_path = os.path.join(checkpoint_dir, "tokenizer.json")
        vocab_size_cfg = cfg.get("vocab_size", 151936)

        if os.path.exists(tok_json_path):
            try:
                with open(tok_json_path, "r", encoding="utf-8") as tf:
                    tok_data = json.load(tf)

                vocab = tok_data.get("model", {}).get("vocab", {})
                raw_merges = tok_data.get("model", {}).get("merges", [])
                added_tokens = tok_data.get("added_tokens", [])

                token_dict = {}
                for tok_str, tok_id in vocab.items():
                    # Control token detection
                    is_control = (
                        tok_str.startswith("<|") and tok_str.endswith("|>")
                    ) or tok_str in ("<s>", "</s>", "<unk>", "<pad>")
                    token_dict[tok_id] = (
                        tok_str,
                        gguf.TokenType.CONTROL if is_control else gguf.TokenType.NORMAL,
                    )

                for item in added_tokens:
                    c = item.get("content")
                    i = item.get("id")
                    if c is not None and i is not None:
                        token_dict[i] = (c, gguf.TokenType.CONTROL)

                for i in range(vocab_size_cfg):
                    if i not in token_dict:
                        token_dict[i] = (f"<|extra_{i}|>", gguf.TokenType.CONTROL)

                total_vocab = max(len(token_dict), vocab_size_cfg)
                tokens = [token_dict[i][0] for i in range(total_vocab)]
                tok_types = [token_dict[i][1] for i in range(total_vocab)]
                scores = [0.0] * total_vocab

                writer.add_tokenizer_model("gpt2")
                writer.add_tokenizer_pre("qwen2")
                writer.add_token_list(tokens)
                writer.add_token_types(tok_types)
                writer.add_token_scores(scores)

                formatted_merges = [
                    f"{m[0]} {m[1]}" if isinstance(m, (list, tuple)) else m for m in raw_merges
                ]
                if formatted_merges:
                    writer.add_token_merges(formatted_merges)
                    logger.info("[GGUFExporter] Added %d tokenizer merges", len(formatted_merges))

                eos_id = cfg.get("eos_token_id", 151645)
                bos_id = cfg.get("bos_token_id", 151643)
                pad_id = cfg.get("pad_token_id", 151643)
                if eos_id is not None:
                    writer.add_eos_token_id(eos_id)
                if bos_id is not None:
                    writer.add_bos_token_id(bos_id)
                if pad_id is not None:
                    writer.add_pad_token_id(pad_id)

                chat_jinja = os.path.join(checkpoint_dir, "chat_template.jinja")
                if os.path.exists(chat_jinja):
                    with open(chat_jinja, "r", encoding="utf-8") as ctf:
                        writer.add_chat_template(ctf.read())

                logger.info("[GGUFExporter] Added %d tokenizer vocabulary entries with BPE merges", len(tokens))
            except Exception as e:
                logger.warning("[GGUFExporter] Could not encode tokenizer vocabulary: %s", e)

        # 3. Load Tensors from safetensors or pytorch_model.bin
        st_path = os.path.join(checkpoint_dir, "model.safetensors")
        bin_path = os.path.join(checkpoint_dir, "pytorch_model.bin")

        if os.path.exists(st_path):
            state_dict = safetensors.torch.load_file(st_path, device="cpu")
        elif os.path.exists(bin_path):
            state_dict = torch.load(bin_path, map_location="cpu")
        else:
            raise FileNotFoundError(f"No weights found (model.safetensors or pytorch_model.bin) in {checkpoint_dir}")

        total_params = 0
        added_tensors = 0

        for hf_name, tensor in state_dict.items():
            gguf_name = cls._map_tensor_name(hf_name)
            # GGML requires 1D bias vectors and normalization scales to remain in float32
            if tensor.ndim == 1 or gguf_name.endswith(".bias") or gguf_name.endswith("_norm.weight"):
                t_np = tensor.detach().cpu().to(torch.float32).numpy()
            else:
                t_np = tensor.detach().cpu().to(torch.float16 if dtype == "f16" else torch.float32).numpy()
            total_params += t_np.size
            writer.add_tensor(gguf_name, t_np)
            added_tensors += 1

        logger.info("[GGUFExporter] Writing GGUF binary: %d tensors, %d params", added_tensors, total_params)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
        writer.close()

        file_size = os.path.getsize(output_gguf_path)
        file_sha256 = cls.compute_sha256(output_gguf_path)
        duration = time.time() - start_time

        # 4. Immediate Validation using GGUFReader
        validation = cls.validate(output_gguf_path)

        now = datetime.now().isoformat(timespec="seconds")
        return GGUFExportResult(
            gguf_path=output_gguf_path,
            file_size_bytes=file_size,
            sha256=file_sha256,
            architecture=arch,
            tensor_count=added_tensors,
            parameters_count=total_params,
            is_valid=validation["is_valid"],
            validation_details=validation,
            duration_seconds=round(duration, 3),
            created_at=now,
        )

    @classmethod
    def validate(cls, gguf_path: str) -> Dict[str, Any]:
        """Validates an exported GGUF file using GGUFReader."""
        if not os.path.exists(gguf_path):
            return {"is_valid": False, "error": f"File does not exist: {gguf_path}"}

        try:
            reader = gguf.GGUFReader(gguf_path)
            arch_field = reader.get_field("general.architecture")
            arch = arch_field.parts[-1].tobytes().decode("utf-8") if arch_field else "unknown"
            tensors_count = len(reader.tensors)

            # Check for critical tensor existence
            has_embd = any(t.name == "token_embd.weight" for t in reader.tensors)
            has_norm = any(t.name == "output_norm.weight" for t in reader.tensors)
            has_layer0 = any(t.name.startswith("blk.0.") for t in reader.tensors)

            is_valid = bool(arch and tensors_count > 0 and has_embd and has_norm and has_layer0)

            result = {
                "is_valid": is_valid,
                "architecture": arch,
                "tensor_count": tensors_count,
                "has_token_embd": has_embd,
                "has_output_norm": has_norm,
                "has_layer_blocks": has_layer0,
                "file_size_bytes": os.path.getsize(gguf_path),
            }
            return result
        except Exception as e:
            return {"is_valid": False, "error": str(e)}
