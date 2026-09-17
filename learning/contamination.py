"""
AURA P1 Contamination Defense.

Checks a training dataset (JSONL, the LearningCandidatePipeline format)
against the immutable held-out evaluation set for:
    - exact duplicate overlap,
    - normalised-text overlap (case/whitespace/diacritic-insensitive),
    - near-duplicate overlap (character trigram Jaccard similarity),
    - prompt leakage (held-out answers appearing in training inputs),
    - answer leakage (held-out input phrasing embedded in training targets).

A contaminated experiment is refused: the promotion gate reads
`contamination_status` and rejects the candidate before any score can
matter. This is a hard gate, not a warning.
"""

from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from learning.heldout import (
    HELD_OUT_SUITE,
    HELDOUT_DATASET_VERSION,
    HELDOUT_EXAMPLE_COUNT,
    HeldOutCase,
    heldout_dataset_hash,
)


def normalize_text(text: str, strip_diacritics: bool = False) -> str:
    """Lowercase, whitespace-folded, punctuation-removed normal form."""
    t = text.lower().strip()
    if strip_diacritics:
        t = "".join(
            ch
            for ch in unicodedata.normalize("NFD", t)
            if unicodedata.category(ch) != "Mn"
        )
    t = re.sub(r"<\|?\w+\|?>", " ", t)  # chat-template markers
    t = re.sub(r"[^\w\sÀ-ỹ]", " ", t)
    return " ".join(t.split())


def trigrams(text: str) -> Set[str]:
    """Character trigrams over the normalised text."""
    t = normalize_text(text).replace(" ", "_")
    if len(t) < 3:
        return {t} if t else set()
    return {t[i : i + 3] for i in range(len(t) - 2)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


NEAR_DUPLICATE_THRESHOLD = 0.5  # trigram Jaccard over normalised text
LEAKAGE_THRESHOLD = 0.6  # similarity for answer-embedding checks


@dataclass
class ContaminationReport:
    train_examples: int
    heldout_examples: int
    exact_overlap_count: int
    normalized_overlap_count: int
    near_duplicate_count: int
    prompt_leakage_count: int
    answer_leakage_count: int
    overlap_count: int  # any overlap of any kind
    contamination_status: str  # "CLEAN" or "CONTAMINATED"
    contaminated_pairs: List[Dict[str, Any]] = field(default_factory=list)
    heldout_version: str = HELDOUT_DATASET_VERSION
    heldout_hash: str = ""
    thresholds: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_examples": self.train_examples,
            "heldout_examples": self.heldout_examples,
            "overlap_count": self.overlap_count,
            "exact_overlap_count": self.exact_overlap_count,
            "normalized_overlap_count": self.normalized_overlap_count,
            "near_duplicate_count": self.near_duplicate_count,
            "prompt_leakage_count": self.prompt_leakage_count,
            "answer_leakage_count": self.answer_leakage_count,
            "contamination_status": self.contamination_status,
            "contaminated_pairs": self.contaminated_pairs,
            "heldout_version": self.heldout_version,
            "heldout_hash": self.heldout_hash,
            "thresholds": self.thresholds,
        }


def _iter_jsonl(path_or_lines: Any) -> Iterable[Dict[str, Any]]:
    """Accepts a dataset file path or a list of JSONL strings."""
    if isinstance(path_or_lines, str):
        with open(path_or_lines, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    else:
        for line in path_or_lines:
            if isinstance(line, dict):
                yield line
            elif line.strip():
                yield json.loads(line)


def check_contamination(
    train_path: str,
    near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD,
    leakage_threshold: float = LEAKAGE_THRESHOLD,
    suite: Optional[List[HeldOutCase]] = None,
    suite_version: Optional[str] = None,
    suite_hash: Optional[str] = None,
) -> ContaminationReport:
    """
    Compares a training dataset against the immutable held-out suite.

    Training inputs are compared against held-out input_text; training
    assistant targets are additionally checked for embedded held-out
    prompts (prompt leakage) and training inputs for embedded held-out
    answers' distinctive content is covered by near-duplicate analysis.
    """

    active_suite = suite if suite is not None else HELD_OUT_SUITE
    s_version = suite_version or (HELDOUT_DATASET_VERSION if suite is None else "custom-suite")
    s_hash = suite_hash or (heldout_dataset_hash() if suite is None else "")

    heldout_inputs = [(c.test_id, c.input_text) for c in active_suite]

    # Pre-compute held-out trigram sets once
    heldout_tri = {tid: trigrams(txt) for tid, txt in heldout_inputs}
    heldout_norm = {tid: normalize_text(txt) for tid, txt in heldout_inputs}

    report = ContaminationReport(
        train_examples=0,
        heldout_examples=len(active_suite),
        exact_overlap_count=0,
        normalized_overlap_count=0,
        near_duplicate_count=0,
        prompt_leakage_count=0,
        answer_leakage_count=0,
        overlap_count=0,
        contamination_status="CLEAN",
        heldout_version=s_version,
        heldout_hash=s_hash,
        thresholds={
            "near_duplicate_jaccard": near_duplicate_threshold,
            "leakage_jaccard": leakage_threshold,
        },
    )

    contaminated_pairs: List[Dict[str, Any]] = []
    train_count = 0
    seen_pairs: Set[Tuple[str, str]] = set()

    for record in _iter_jsonl(train_path):
        train_count += 1
        messages = record.get("messages", [])
        train_input = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"), ""
        )
        train_target = "\n".join(
            str(m.get("content", "")) + json.dumps(m.get("tool_calls", []), ensure_ascii=False)
            for m in messages
            if m.get("role") == "assistant"
        )
        train_tri = trigrams(train_input)
        train_norm = normalize_text(train_input)

        for tid, htext in heldout_inputs:
            kinds = []

            # 1. Exact
            if train_input.strip() == htext.strip():
                kinds.append("exact")

            # 2. Normalised
            if train_norm and train_norm == heldout_norm[tid]:
                kinds.append("normalized")

            # 3. Near-duplicate (input vs input)
            sim = jaccard(train_tri, heldout_tri[tid])
            if sim >= near_duplicate_threshold:
                kinds.append("near_duplicate")

            # 4. Prompt leakage: held-out prompt embedded in training target
            if htext.strip() and htext.strip().lower() in train_target.lower():
                kinds.append("prompt_leakage")

            # 5. Answer leakage: training input embeds held-out answer phrasing
            #    (rare; covered via near-duplicate of distinctive answer terms)
            if any(
                req.lower() in train_input.lower()
                for c in HELD_OUT_SUITE
                if c.test_id == tid
                for req in c.must_contain
                if len(req) > 8
            ):
                kinds.append("answer_leakage")

            if kinds:
                key = (record.get("id", f"row{train_count}"), tid)
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    contaminated_pairs.append(
                        {
                            "train_example_id": key[0],
                            "heldout_test_id": tid,
                            "kinds": kinds,
                            "similarity": round(sim, 4) if "near_duplicate" in kinds else None,
                            "train_input_snippet": train_input[:60],
                        }
                    )
                for k in kinds:
                    if k == "exact":
                        report.exact_overlap_count += 1
                    elif k == "normalized":
                        report.normalized_overlap_count += 1
                    elif k == "near_duplicate":
                        report.near_duplicate_count += 1
                    elif k == "prompt_leakage":
                        report.prompt_leakage_count += 1
                    elif k == "answer_leakage":
                        report.answer_leakage_count += 1
                report.overlap_count = len(contaminated_pairs)

    report.train_examples = train_count
    report.contaminated_pairs = contaminated_pairs
    report.overlap_count = len(contaminated_pairs)
    if contaminated_pairs:
        report.contamination_status = "CONTAMINATED"

    return report
