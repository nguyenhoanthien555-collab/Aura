"""
Local AURA Brain Provider.

First-class local provider integrating LocalModelRuntime and BrainManager into the
AURA provider abstraction. Natively supports generate, stream, and generate_with_tools
without requiring any remote cloud API or API keys.
"""

from typing import Any, Dict, Iterator, List, Optional

from brain.local_runtime import DeterministicBackend, HttpInferenceBackend, LocalModelRuntime
from brain.native_fc import ModelTurn
from brain.package import BrainManager, BrainManifest, BrainPackage, BrainStatus
from brain.ports import LLM
from brain.provenance import BrainProvenance
from core.logger import logger


class LocalAuraBrain(LLM):
    """
    First-class Local Brain for AURA.
    Default native intelligence for chat, agent tasks, tools, and learning.
    """

    provider_name = "local_aura"
    label = "AURA Local Brain"

    def __init__(
        self,
        runtime: Optional[LocalModelRuntime] = None,
        manager: Optional[BrainManager] = None,
        auto_load: bool = True,
    ):
        self.manager = manager or BrainManager()
        self.runtime = runtime or LocalModelRuntime()

        if auto_load:
            self._ensure_active_brain()

    def _ensure_active_brain(self) -> None:
        """Loads the active brain package or initializes a baseline package."""
        active_pkg = self.manager.get_active_package()
        if active_pkg is None:
            # Create and register default baseline local brain v1
            manifest = BrainManifest(
                brain_id="aura-local-v1",
                version="1.0.0",
                model_format="deterministic",
                context_length=self.runtime.hardware.maximum_safe_context,
                parameter_count="7B",
                quantization="Q4_K_M",
                runtime_backend=self.runtime.hardware.recommended_backend,
                status=BrainStatus.ACTIVE.value,
                training_lineage={"base_model": "aura-base-7b", "supervised_fine_tuning": "baseline"},
                capabilities=["function_calling", "streaming", "reasoning", "clarification", "confirmation"],
            )
            active_pkg = self.manager.register_package(manifest)
            self.manager.promote_candidate(active_pkg.brain_id)

        self.runtime.load_package(active_pkg)
        logger.info("LocalAuraBrain initialized with package %s (v%s)", active_pkg.brain_id, active_pkg.version)

    @property
    def brain_id(self) -> str:
        if self.runtime.active_package:
            return self.runtime.active_package.brain_id
        return "aura-local-unloaded"

    @property
    def version(self) -> str:
        if self.runtime.active_package:
            return self.runtime.active_package.version
        return "0.0.0"

    def get_provenance(self) -> BrainProvenance:
        return self.runtime.get_provenance()

    def generate(self, prompt: str) -> str:
        """Generates a text completion."""
        return self.runtime.generate(prompt)

    def stream(self, prompt: str) -> Iterator[str]:
        """Streams text fragments."""
        return self.runtime.stream(prompt)

    def generate_with_tools(
        self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurn:
        """Generates a native function calling turn."""
        return self.runtime.generate_with_tools(system, messages, tools)

    def health(self) -> str:
        return self.runtime.health()
