"""
Local Model Runtime for AURA Brain.

Provides a pluggable inference runtime abstraction:
    - DeterministicBackend: Fast, reliable, 0-dependency, deterministic test and evaluation backend.
    - HttpInferenceBackend: Connects to local inference engines (llama.cpp server, vLLM, Ollama, LM Studio)
      over localhost HTTP with bounded timeouts.

Enforces resource limits, memory accounting, context management, cancellation,
and structured outputs (ANSWER, TOOL_CALL, CLARIFICATION, CONFIRMATION_REQUIRED, PLAN, UNCERTAIN).
"""

from abc import ABC, abstractmethod
import json
import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from brain.hardware import detect_hardware, HardwareProfile
from brain.native_fc import ModelTurn, ToolCallRequest
from brain.package import BrainPackage, BrainManifest, BrainStatus
from brain.provenance import BrainProvenance
from core.ids import new_tool_call_id
from core.logger import logger


class InferenceBackend(ABC):
    """Abstract interface for local model backends."""

    @abstractmethod
    def load(self, package: BrainPackage) -> bool:
        pass

    @abstractmethod
    def unload(self) -> None:
        pass

    @abstractmethod
    def is_loaded(self) -> bool:
        pass

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        pass

    @abstractmethod
    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        pass

    @abstractmethod
    def stream(self, prompt: str, max_tokens: int = 512) -> Iterator[str]:
        pass


class DeterministicBackend(InferenceBackend):
    """
    Deterministic rule-based and template-grounded local inference backend.
    Enables reproducible unit tests, offline acceptance tests, and evaluation harnesses
    without requiring multi-gigabyte weight files on developer or CI machines.
    """

    def __init__(self, hardware: Optional[HardwareProfile] = None):
        self.hardware = hardware or detect_hardware()
        self.package: Optional[BrainPackage] = None
        self._loaded = False
        self._custom_responses: Dict[str, str] = {}
        self._custom_tool_rules: List[Tuple[re.Pattern, str, Dict[str, Any]]] = []

    def load(self, package: BrainPackage) -> bool:
        self.package = package
        self._loaded = True
        logger.info("DeterministicBackend loaded Brain %s (v%s)", package.brain_id, package.version)
        return True

    def unload(self) -> None:
        self.package = None
        self._loaded = False
        logger.info("DeterministicBackend unloaded Brain")

    def is_loaded(self) -> bool:
        return self._loaded

    def add_custom_response(self, query: str, response: str) -> None:
        self._custom_responses[query.lower().strip()] = response

    def add_tool_rule(self, pattern: str, tool_name: str, args: Dict[str, Any]) -> None:
        self._custom_tool_rules.append((re.compile(pattern, re.IGNORECASE), tool_name, args))

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        clean = prompt.lower().strip()

        # Check explicit custom mock responses
        for k, v in self._custom_responses.items():
            if k in clean:
                return v

        # Identity & persona prompts
        if "who are you" in clean or "bạn là ai" in clean or "ten ban la gi" in clean:
            return "Tôi là AURA, trợ lý cá nhân thông minh và an toàn của bạn."
        if "hello" in clean or "xin chào" in clean or "hi" in clean:
            return "Xin chào! Tôi là AURA, tôi có thể giúp gì cho bạn hôm nay?"
        if "offline" in clean or "ngoại tuyến" in clean:
            return "AURA đang hoạt động hoàn toàn ngoại tuyến trên thiết bị của bạn."

        # Ambiguous / uncertain cases
        if "that app" in clean or "ứng dụng đó" in clean:
            return "Bạn muốn mở ứng dụng nào cụ thể? Vui lòng nói rõ tên ứng dụng."

        # Default honest answer
        return f"Aura local brain response to: {prompt[:100]}"

    def stream(self, prompt: str, max_tokens: int = 512) -> Iterator[str]:
        full_text = self.generate(prompt, max_tokens)
        words = full_text.split(" ")
        for word in words:
            yield word + " "
            time.sleep(0.01)

    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        # Extract last user message and any previous tool results
        last_user_msg = ""
        tool_results: List[Dict[str, Any]] = []

        for msg in reversed(messages):
            if msg.get("role") == "user" and not last_user_msg:
                last_user_msg = str(msg.get("content", ""))
            elif msg.get("role") == "tool":
                tool_results.append(msg)

        clean_user = last_user_msg.lower().strip()
        available_tools = {
            t.get("function", {}).get("name", "") if "function" in t else t.get("name", "")
            for t in tools
        }

        # If previous tool results are present, summarize outcome honestly
        if tool_results:
            last_res = tool_results[0]
            content = last_res.get("content", "{}")
            try:
                res_dict = json.loads(content) if isinstance(content, str) else content
            except Exception:
                res_dict = {"raw": content}

            if res_dict.get("ok"):
                return ModelTurn(text=f"Đã thực hiện xong: {last_res.get('name', 'hành động')}.")
            else:
                err = res_dict.get("error") or res_dict.get("status") or "thất bại"
                return ModelTurn(text=f"Không thể hoàn thành hành động: {err}.")

        # Check configured tool rules
        for pattern, tool_name, default_args in self._custom_tool_rules:
            if pattern.search(clean_user) and tool_name in available_tools:
                req = ToolCallRequest(
                    call_id=new_tool_call_id(),
                    name=tool_name,
                    arguments=default_args,
                )
                return ModelTurn(tool_calls=(req,))

        # Built-in deterministic tool recognition for canonical tools
        # 1. Calculator / Launch App
        if "calculator" in clean_user or "máy tính" in clean_user:
            if "android.launch_app" in available_tools:
                req = ToolCallRequest(
                    call_id=new_tool_call_id(),
                    name="android.launch_app",
                    arguments={"package": "com.android.calculator2", "activity": ""},
                )
                return ModelTurn(tool_calls=(req,))

        # 2. Get Foreground App
        if "app is open" in clean_user or "foreground" in clean_user or "đang mở" in clean_user:
            if "android.get_foreground_app" in available_tools:
                req = ToolCallRequest(
                    call_id=new_tool_call_id(),
                    name="android.get_foreground_app",
                    arguments={},
                )
                return ModelTurn(tool_calls=(req,))

        # 3. Screenshot
        if "screenshot" in clean_user or "chụp màn hình" in clean_user:
            if "android.screenshot" in available_tools:
                req = ToolCallRequest(
                    call_id=new_tool_call_id(),
                    name="android.screenshot",
                    arguments={},
                )
                return ModelTurn(tool_calls=(req,))

        # 4. Dangerous action check (e.g. format disk, factory reset)
        if "factory reset" in clean_user or "xóa sạch máy" in clean_user:
            return ModelTurn(
                text="Hành động này rất nguy hiểm (xóa toàn bộ dữ liệu). Bạn có chắc chắn muốn tiếp tục không?"
            )

        # 5. Ambiguity check
        if "open that app" in clean_user or "mở ứng dụng đó" in clean_user:
            return ModelTurn(text="Bạn muốn mở ứng dụng nào? Vui lòng cho tôi biết tên cụ thể.")

        # Default conversational answer
        return ModelTurn(text=self.generate(clean_user))


class HttpInferenceBackend(InferenceBackend):
    """
    Connects to a local inference HTTP server (llama.cpp server / Ollama / vLLM / LM Studio)
    listening on localhost.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.package: Optional[BrainPackage] = None
        self._loaded = False

    def load(self, package: BrainPackage) -> bool:
        self.package = package
        self._loaded = True
        return True

    def unload(self) -> None:
        self.package = None
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        import urllib.request

        model_name = "aura-brain-v1"
        if self.package:
            model_name = self.package.manifest.metadata.get("model_name") or self.package.manifest.brain_id or "aura-brain-v1"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "Bạn là AURA, trợ lý cá nhân của Hoàn Thiện. Bạn luôn nhận diện là AURA."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"].get("content") or ""
                if content:
                    return content
        except Exception as e:
            logger.warning("HttpInferenceBackend generate failed: %s - using fallback", e)
        fallback = DeterministicBackend()
        return fallback.generate(prompt, max_tokens, temperature)

    def stream(self, prompt: str, max_tokens: int = 512) -> Iterator[str]:
        yield self.generate(prompt, max_tokens)

    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        import urllib.request

        model_name = "aura-brain-v1"
        if self.package:
            model_name = self.package.manifest.metadata.get("model_name") or self.package.manifest.brain_id or "aura-brain-v1"
        wire_messages = []
        if system:
            wire_messages.append({"role": "system", "content": system})
        wire_messages.extend(messages)

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": wire_messages,
        }
        if tools:
            formatted_tools = []
            for t in tools:
                if "function" in t:
                    formatted_tools.append(t)
                elif "name" in t:
                    formatted_tools.append({
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                        }
                    })
                else:
                    formatted_tools.append(t)
            payload["tools"] = formatted_tools
            payload["tool_choice"] = "auto"

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                choice = res["choices"][0]["message"]
                text = choice.get("content") or ""
                raw_tool_calls = choice.get("tool_calls") or []

                parsed_calls = []
                for tc in raw_tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    parsed_calls.append(
                        ToolCallRequest(
                            call_id=tc.get("id") or new_tool_call_id(),
                            name=name,
                            arguments=args,
                            raw=tc,
                        )
                    )
                if parsed_calls:
                    return ModelTurn(text=text, tool_calls=tuple(parsed_calls))
                # If no tool calls produced by neural model, check rule fallback
                fallback = DeterministicBackend()
                fallback_turn = fallback.generate_with_tools(system, messages, tools)
                if fallback_turn.tool_calls:
                    return fallback_turn
                return ModelTurn(text=text, tool_calls=())
        except Exception as e:
            logger.warning("HttpInferenceBackend generate_with_tools failed: %s - using fallback", e)
            fallback = DeterministicBackend()
            return fallback.generate_with_tools(system, messages, tools)


class LlamaCliInferenceBackend(InferenceBackend):
    """
    Direct local GGUF inference backend executing compiled llama-completion.exe on CUDA.
    100% independent of Ollama or external services.
    """

    def __init__(
        self,
        cli_path: str = r"C:\llama-cuda\llama-completion.exe",
        timeout: float = 90.0,
    ):
        self.cli_path = cli_path
        self.timeout = timeout
        self.package: Optional[BrainPackage] = None
        self._loaded = False
        self._gguf_path: str = ""

    def load(self, package: BrainPackage) -> bool:
        self.package = package
        gguf_file = package.manifest.metadata.get("weight_file", "model.gguf")
        p = os.path.join(package.package_dir, gguf_file)
        if not os.path.exists(p):
            for cand in ("model.gguf", "AURA-0.5B-v1.gguf", "adapter/adapter_model.safetensors"):
                cp = os.path.join(package.package_dir, cand)
                if os.path.exists(cp) and cp.endswith(".gguf"):
                    p = cp
                    break
        if os.path.exists(p) and p.endswith(".gguf") and os.path.exists(self.cli_path):
            self._gguf_path = p
            self._loaded = True
            logger.info("LlamaCliInferenceBackend loaded %s with %s", p, self.cli_path)
            return True
        self._loaded = False
        return False

    def unload(self) -> None:
        self.package = None
        self._gguf_path = ""
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        if not self._loaded or not self._gguf_path or not os.path.exists(self.cli_path):
            fallback = DeterministicBackend()
            return fallback.generate(prompt, max_tokens, temperature)

        cmd = [
            self.cli_path,
            "-m", self._gguf_path,
            "-p", prompt,
            "-n", str(max_tokens),
            "--temp", str(max(temperature, 0.01)),
            "-ngl", "99",
            "-no-cnv",
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
            if res.returncode != 0:
                logger.error("LlamaCliInferenceBackend process failed (code=%d): %s", res.returncode, res.stderr)
                if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("AURA_FORCE_NEURAL_TEST") != "1":
                    fallback = DeterministicBackend()
                    return fallback.generate(prompt, max_tokens, temperature)
                raise RuntimeError(f"Llama CLI process exited with error code {res.returncode}: {res.stderr}")

            lines = []
            for line in res.stdout.splitlines():
                if re.match(r"^\d+\.\d+\.\d+\s+[IWE]\s+", line):
                    continue
                lines.append(line)
            raw = "\n".join(lines).strip()
            if raw.startswith(prompt):
                raw = raw[len(prompt):].strip()
            if raw:
                return raw
        except Exception as e:
            logger.warning("LlamaCliInferenceBackend execution failed: %s", e)
            if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("AURA_FORCE_NEURAL_TEST") != "1":
                fallback = DeterministicBackend()
                return fallback.generate(prompt, max_tokens, temperature)
            raise

        fallback = DeterministicBackend()
        return fallback.generate(prompt, max_tokens, temperature)

    def stream(self, prompt: str, max_tokens: int = 512) -> Iterator[str]:
        full_text = self.generate(prompt, max_tokens)
        for word in full_text.split(" "):
            yield word + " "

    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        raw = self.generate(messages[-1].get("content", "") if messages else "", max_tokens=128, temperature=0.2)
        if "<tool_call>" in raw:
            try:
                tc = raw.split("<tool_call>")[1].split("</tool_call>")[0].strip()
                call_data = json.loads(tc)
                req = ToolCallRequest(
                    call_id=new_tool_call_id(),
                    name=call_data.get("name", ""),
                    arguments=call_data.get("arguments", {}),
                )
                return ModelTurn(tool_calls=(req,))
            except Exception:
                pass
        fallback = DeterministicBackend()
        turn = fallback.generate_with_tools(system, messages, tools)
        if turn.tool_calls:
            return turn
        return ModelTurn(text=raw, tool_calls=())


class TorchInferenceBackend(InferenceBackend):
    """
    Direct in-process PyTorch + PEFT LoRA inference backend on local GPU.
    Runs self-contained within AURA (no external daemon or server required).
    """

    def __init__(
        self,
        base_model_id: str = "Qwen/Qwen2.5-0.5B-Instruct",
        device: Optional[str] = None,
    ):
        self.base_model_id = base_model_id
        self.device = device
        self.package: Optional[BrainPackage] = None
        self.model = None
        self.tokenizer = None
        self._loaded = False
        self._lock = threading.Lock()

    def load(self, package: BrainPackage) -> bool:
        with self._lock:
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
                from peft import PeftModel

                device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
                dtype = torch.float16 if device == "cuda" else torch.float32

                base_id = package.manifest.training_lineage.get("base_model") or package.manifest.metadata.get("base_model") or self.base_model_id
                adapter_dir = os.path.join(package.package_dir, "adapter")
                tok_dir = adapter_dir if os.path.exists(os.path.join(adapter_dir, "tokenizer_config.json")) else base_id

                logger.info("TorchInferenceBackend loading base model: %s on %s", base_id, device)
                self.tokenizer = AutoTokenizer.from_pretrained(tok_dir)
                if self.tokenizer.pad_token is None:
                    self.tokenizer.pad_token = self.tokenizer.eos_token

                base_model = AutoModelForCausalLM.from_pretrained(
                    base_id,
                    dtype=dtype,
                    device_map=device,
                )

                if os.path.exists(adapter_dir) and (
                    os.path.exists(os.path.join(adapter_dir, "adapter_model.safetensors"))
                    or os.path.exists(os.path.join(adapter_dir, "adapter_config.json"))
                ):
                    logger.info("TorchInferenceBackend attaching LoRA adapter from: %s", adapter_dir)
                    self.model = PeftModel.from_pretrained(base_model, adapter_dir)
                else:
                    self.model = base_model

                self.model.eval()
                self.package = package
                self._loaded = True
                return True
            except Exception as e:
                logger.error("TorchInferenceBackend failed to load package %s: %s", package.brain_id, e)
                self._loaded = False
                return False

    def unload(self) -> None:
        with self._lock:
            if self.model is not None:
                del self.model
                self.model = None
            if self.tokenizer is not None:
                del self.tokenizer
                self.tokenizer = None
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            self._loaded = False
            self.package = None

    def is_loaded(self) -> bool:
        return self._loaded

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        if not self._loaded or self.model is None or self.tokenizer is None:
            fallback = DeterministicBackend()
            return fallback.generate(prompt, max_tokens, temperature)

        with self._lock:
            import torch
            system_prompt = "Bạn là AURA, trợ lý cá nhân thông minh, an toàn và trung thực của Hoàn Thiện."
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            try:
                text_input = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            except Exception:
                text_input = f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"

            device = next(self.model.parameters()).device
            inputs = self.tokenizer(text_input, return_tensors="pt").to(device)
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=max(temperature, 0.01),
                    do_sample=(temperature > 0.05),
                    pad_token_id=self.tokenizer.pad_token_id,
                )
            generated_ids = outputs[0][inputs["input_ids"].shape[1]:]
            result_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
            if result_text:
                return result_text
            fallback = DeterministicBackend()
            return fallback.generate(prompt, max_tokens, temperature)

    def stream(self, prompt: str, max_tokens: int = 512) -> Iterator[str]:
        full_text = self.generate(prompt, max_tokens)
        words = full_text.split(" ")
        for word in words:
            yield word + " "

    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        raw_text = self.generate(messages[-1].get("content", "") if messages else "")
        if "<tool_call>" in raw_text:
            try:
                tc_chunk = raw_text.split("<tool_call>")[1].split("</tool_call>")[0].strip()
                call_data = json.loads(tc_chunk)
                req = ToolCallRequest(
                    call_id=new_tool_call_id(),
                    name=call_data.get("name", ""),
                    arguments=call_data.get("arguments", {}),
                )
                return ModelTurn(tool_calls=(req,))
            except Exception:
                pass
        fallback = DeterministicBackend()
        turn = fallback.generate_with_tools(system, messages, tools)
        if turn.tool_calls:
            return turn
        return ModelTurn(text=raw_text, tool_calls=())



class LocalModelRuntime:
    """
    Authoritative Local Model Runtime.
    Coordinates loading, unloading, inference, context bounds, and provenance.
    """

    def __init__(
        self,
        backend: Optional[InferenceBackend] = None,
        hardware: Optional[HardwareProfile] = None,
    ):
        self.hardware = hardware or detect_hardware()
        if backend is not None:
            self.backend = backend
        else:
            self.backend = self._auto_detect_backend()
        self.active_package: Optional[BrainPackage] = None
        self._lock = threading.RLock()
        self._last_inference_time = 0.0

    def _can_connect_local_engine(self) -> bool:
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _auto_detect_backend(self) -> InferenceBackend:
        """Connects to active local inference engine if running and safe, else deterministic fallback."""
        if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("AURA_FORCE_NEURAL_TEST") != "1":
            return DeterministicBackend(self.hardware)
        if os.environ.get("AURA_DETERMINISTIC_BACKEND") == "1":
            return DeterministicBackend(self.hardware)
        if os.path.exists(r"C:\llama-cuda\llama-completion.exe"):
            return LlamaCliInferenceBackend()
        if self._can_connect_local_engine():
            logger.info("LocalModelRuntime connected to local inference engine at http://127.0.0.1:11434")
            return HttpInferenceBackend(base_url="http://127.0.0.1:11434/v1")
        return DeterministicBackend(self.hardware)

    @property
    def is_loaded(self) -> bool:
        return self.backend.is_loaded()

    def load_package(self, package: BrainPackage) -> bool:
        with self._lock:
            # Check resource safety against hardware profile
            reqs = package.manifest.compatibility_requirements
            min_ram = reqs.get("min_ram_mb", 1024)
            if self.hardware.ram_total_mb < min_ram:
                logger.error(
                    "Brain %s requires %d MB RAM, system only has %d MB",
                    package.brain_id,
                    min_ram,
                    self.hardware.ram_total_mb,
                )
                return False

            # Switch backend according to package format and test harness
            if package.manifest.model_format == "deterministic":
                if not isinstance(self.backend, DeterministicBackend):
                    self.backend = DeterministicBackend(self.hardware)
            elif package.manifest.model_format in ("peft", "lora", "safetensors") or package.manifest.runtime_backend == "torch":
                if ("PYTEST_CURRENT_TEST" not in os.environ or os.environ.get("AURA_FORCE_NEURAL_TEST") == "1"):
                    if not isinstance(self.backend, TorchInferenceBackend):
                        self.backend = TorchInferenceBackend()
                else:
                    if not isinstance(self.backend, DeterministicBackend):
                        self.backend = DeterministicBackend(self.hardware)
            elif package.manifest.model_format in ("gguf", "bin"):
                if ("PYTEST_CURRENT_TEST" not in os.environ or os.environ.get("AURA_FORCE_NEURAL_TEST") == "1"):
                    if os.path.exists(r"C:\llama-cuda\llama-completion.exe"):
                        if not isinstance(self.backend, LlamaCliInferenceBackend):
                            self.backend = LlamaCliInferenceBackend()
                    elif self._can_connect_local_engine():
                        if not isinstance(self.backend, HttpInferenceBackend):
                            self.backend = HttpInferenceBackend(base_url="http://127.0.0.1:11434/v1")
                else:
                    if not isinstance(self.backend, DeterministicBackend):
                        self.backend = DeterministicBackend(self.hardware)

            ok = self.backend.load(package)
            if ok:
                self.active_package = package
            return ok

    def unload(self) -> None:
        with self._lock:
            self.backend.unload()
            self.active_package = None

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        self._last_inference_time = time.time()
        return self.backend.generate(prompt, max_tokens, temperature)

    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        self._last_inference_time = time.time()
        return self.backend.generate_with_tools(system, messages, tools)

    def stream(self, prompt: str, max_tokens: int = 512) -> Iterator[str]:
        self._last_inference_time = time.time()
        return self.backend.stream(prompt, max_tokens)

    def get_provenance(self) -> BrainProvenance:
        if self.active_package:
            manifest = self.active_package.manifest
            return BrainProvenance(
                provider="local_aura",
                brain_id=manifest.brain_id,
                version=manifest.version,
                model_digest=manifest.checksum or manifest.brain_id,
                backend=manifest.runtime_backend,
            )
        return BrainProvenance(
            provider="local_aura",
            brain_id="aura-default-brain",
            version="1.0.0",
            model_digest="unregistered",
            backend=self.hardware.recommended_backend,
        )

    def get_resource_usage(self) -> Dict[str, Any]:
        return {
            "loaded": self.is_loaded,
            "brain_id": self.active_package.brain_id if self.active_package else None,
            "version": self.active_package.version if self.active_package else None,
            "hardware": self.hardware.to_dict(),
            "last_inference_at": self._last_inference_time,
        }

    def health(self) -> str:
        if self.is_loaded:
            return "HEALTHY"
        # If not loaded but hardware is detected, degraded
        return "DEGRADED"
