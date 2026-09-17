"""
AURA Learning Candidate Pipeline.

Extracts high-quality, privacy-screened experiences from the AuraExperienceStore,
deduplicates and validates them, formats them into structured training dataset artifacts
under data/aura/, and prepares dataset manifests for training jobs.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from core.logger import logger
from learning.experience import AuraExperienceStore, Experience


@dataclass
class DatasetManifest:
    dataset_id: str
    num_examples: int
    categories: Dict[str, int]
    file_path: str
    checksum: str
    created_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class LearningCandidatePipeline:
    """Pipelines experiences into structured training datasets for AURA Brain."""

    def __init__(
        self,
        store: Optional[AuraExperienceStore] = None,
        data_dir: str = "data/aura",
    ):
        self.store = store or AuraExperienceStore()
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)

    def _example_signature(self, exp: Experience) -> str:
        """Fingerprint for deduplicating repeated experiences."""
        key = f"{exp.input_text.strip().lower()}|{exp.selected_tool}|{json.dumps(exp.arguments, sort_keys=True)}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def generate_candidate_dataset(
        self,
        min_quality: float = 0.6,
        limit: int = 200,
        dataset_name: str = "aura_learning_dataset",
    ) -> Optional[DatasetManifest]:
        """
        Gathers eligible experiences, formats them as chat/tool-use instruction pairs,
        and saves as a JSONL dataset with manifest.
        """
        experiences = self.store.list_eligible_experiences(min_quality=min_quality, limit=limit)
        if not experiences:
            logger.info("No eligible experiences found for dataset generation")
            return None

        seen_signatures = set()
        formatted_examples = []
        categories_count: Dict[str, int] = {}
        quarantined_contradictions: List[Dict[str, Any]] = []

        from learning.contradiction import ContradictionDetector

        for exp in experiences:
            sig = self._example_signature(exp)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            # Contradiction and capability screening
            exp_audit_dict = {
                "input_text": exp.input_text,
                "final_response": exp.final_response,
                "model_decision": exp.model_decision,
                "selected_tool": exp.selected_tool,
                "arguments": exp.arguments,
                "user_feedback": exp.user_feedback,
            }
            c_verdict = ContradictionDetector.check_experience(exp_audit_dict)
            if not c_verdict.is_valid:
                logger.warning(
                    "[LearningPipeline] Quarantining contradictory experience %s: %s (%s)",
                    exp.experience_id,
                    c_verdict.reason,
                    c_verdict.contradiction_type,
                )
                quarantined_contradictions.append({
                    "id": exp.experience_id,
                    "reason": c_verdict.reason,
                    "type": c_verdict.contradiction_type,
                })
                continue

            # Build standard training turn
            user_turn = {"role": "user", "content": exp.input_text}
            assistant_turn: Dict[str, Any] = {"role": "assistant"}

            if exp.model_decision == "TOOL_CALL" and exp.selected_tool:
                assistant_turn["tool_calls"] = [
                    {
                        "name": exp.selected_tool,
                        "arguments": exp.arguments,
                    }
                ]
                assistant_turn["content"] = ""
                category = "tool_use"
            elif exp.model_decision == "CLARIFICATION":
                assistant_turn["content"] = exp.final_response
                category = "clarification"
            elif exp.model_decision == "CONFIRMATION_REQUIRED":
                assistant_turn["content"] = exp.final_response
                category = "confirmation"
            else:
                if not exp.final_response:
                    # No meaningful target to learn from (e.g. seed rows
                    # without a recorded reply). Training "Verified outcome:
                    # SUCCESS" as an answer would teach the model garbage.
                    continue
                assistant_turn["content"] = exp.final_response
                category = "conversation"

            # Track category count
            categories_count[category] = categories_count.get(category, 0) + 1

            # Determine inclusion reason
            if exp.verifier_result == "VERIFIED":
                inc_reason = "Verified successful execution with physical evidence"
            elif exp.user_feedback in ("CORRECT", "USER_CORRECTED"):
                inc_reason = f"Explicit human feedback: {exp.user_feedback}"
            elif exp.model_decision in ("CLARIFICATION", "CONFIRMATION_REQUIRED"):
                inc_reason = f"Validated safety / clarity protocol: {exp.model_decision}"
            else:
                inc_reason = "High-quality operation without contradiction"

            example = {
                "id": exp.experience_id,
                "category": category,
                "taxonomy_tag": getattr(exp, "taxonomy_tag", "UNVERIFIED") or "UNVERIFIED",
                "quality_score": exp.quality_score,
                "verifier_result": exp.verifier_result,
                "provenance": {
                    "source": "experience_store",
                    "session_id": exp.session_id,
                    "task_id": exp.task_id,
                    "run_id": exp.run_id,
                    "taxonomy_tag": getattr(exp, "taxonomy_tag", "UNVERIFIED") or "UNVERIFIED",
                    "inclusion_reason": inc_reason,
                    "filtering": ["privacy_screen_passed", "deduplicated", "contradiction_screened", f"min_quality_{min_quality}"],
                    "dataset_version": dataset_name,
                    "recorded_at": exp.created_at,
                },
                "messages": [
                    {"role": "system", "content": "You are AURA, a local-first personal AI companion."},
                    user_turn,
                    assistant_turn,
                ],
            }
            formatted_examples.append(example)

        # Include Stable Core Curriculum replay to anchor foundational safety, honesty, and identity
        try:
            from learning.curriculum import get_stable_core_examples
            core_examples = get_stable_core_examples()
            for c_ex in core_examples:
                c_cat = c_ex.get("category", "foundation")
                categories_count[c_cat] = categories_count.get(c_cat, 0) + 1
                formatted_examples.append(c_ex)
            core_count = len(core_examples)
        except Exception as e:
            logger.warning("[LearningPipeline] Failed to load stable core curriculum: %s", e)
            core_count = 0

        if not formatted_examples:
            return None

        # Write dataset file with strict LF newline to prevent Windows CRLF hash divergence
        dataset_filename = f"{dataset_name}.jsonl"
        dataset_path = os.path.join(self.data_dir, dataset_filename)

        hasher = hashlib.sha256()
        with open(dataset_path, "w", encoding="utf-8", newline="\n") as f:
            for ex in formatted_examples:
                line = json.dumps(ex, ensure_ascii=False) + "\n"
                hasher.update(line.encode("utf-8"))
                f.write(line)

        checksum = hasher.hexdigest()
        from datetime import datetime

        now = datetime.now().isoformat(timespec="seconds")

        manifest = DatasetManifest(
            dataset_id=f"ds_{dataset_name}",
            num_examples=len(formatted_examples),
            categories=categories_count,
            file_path=dataset_path,
            checksum=checksum,
            created_at=now,
            metadata={
                "version": dataset_name,
                "min_quality_threshold": min_quality,
                "provenance_tracked": True,
                "core_curriculum_included": core_count > 0,
                "core_curriculum_examples": core_count,
                "quarantined_contradictions": len(quarantined_contradictions),
                "quarantine_log": quarantined_contradictions,
            },
        )

        manifest_path = os.path.join(self.data_dir, f"{dataset_name}_manifest.json")
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest.to_dict(), f, indent=2)

        logger.info(
            "Generated dataset %s with %d examples across categories %s (checksum: %s, core: %d)",
            dataset_filename,
            len(formatted_examples),
            categories_count,
            checksum[:8],
            core_count,
        )

        return manifest
