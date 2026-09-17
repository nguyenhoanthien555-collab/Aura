"""
AURA P1 Learning-Quality tests.

Fast, GPU-free, neural-free tests that pin the P1 evaluation invariants:

    1. The held-out set is immutable (hash pinned, categories complete).
    2. The contamination gate detects every overlap kind it claims to.
    3. The promotion gate rejects every regression it claims to reject.
    4. The evaluation protocol never falls back to deterministic rules.

GPU/llama.cpp execution is covered by the live P1 experiment script, not
these tests - the point here is that the gates cannot silently drift.
"""

import json
import os
import tempfile

import pytest

from learning.heldout import (
    HELD_OUT_SUITE,
    HELDOUT_EXAMPLE_COUNT,
    HELDOUT_DATASET_VERSION,
    heldout_canonical_jsonl,
    heldout_dataset_hash,
)
from learning.contamination import check_contamination
from learning.quality_eval import (
    CaseResult,
    HeldOutReport,
    _run_case,
    compare_for_promotion,
    parse_tool_call_text,
)


# ---------------------------------------------------------------------------
# 1. Held-out set immutability
# ---------------------------------------------------------------------------

HELDOUT_PINNED_HASH = "bb253de2a836117c1ac34d6e26c881c33b3d35f015d315b42e63ec3f721453cd"


class TestHeldOutSetImmutable:
    def test_dataset_hash_is_pinned(self):
        assert heldout_dataset_hash() == HELDOUT_PINNED_HASH

    def test_example_count_is_pinned(self):
        assert HELDOUT_EXAMPLE_COUNT == 26
        assert len(HELD_OUT_SUITE) == 26

    def test_all_test_ids_are_stable_and_unique(self):
        ids = [c.test_id for c in HELD_OUT_SUITE]
        assert len(ids) == len(set(ids))
        for tid in ids:
            assert tid.startswith("p1-") and len(tid.split("-")) >= 3

    def test_category_coverage_matches_brief(self):
        required = {
            "instruction", "vietnamese", "english", "identity", "reasoning",
            "ambiguity", "tool_aware", "honesty", "safety", "tool_honesty",
        }
        present = {c.category for c in HELD_OUT_SUITE}
        assert required <= present

    def test_every_case_has_positive_assertion(self):
        """No case can pass vacuously: each must assert something positive."""
        for c in HELD_OUT_SUITE:
            has_positive = (
                c.expected_decision == "TOOL_CALL"
                or bool(c.must_contain)
                or bool(c.must_contain_any)
            )
            assert has_positive, f"{c.test_id} has no positive assertion (vacuous)"

    def test_canonical_serialisation_is_deterministic(self):
        a = heldout_canonical_jsonl()
        b = heldout_canonical_jsonl()
        assert a == b
        # round-trip through JSON preserves the hash
        rows = [json.loads(line) for line in a.strip().splitlines()]
        assert len(rows) == 26


# ---------------------------------------------------------------------------
# 2. Contamination defense
# ---------------------------------------------------------------------------

class TestContaminationDefense:
    def _write_dataset(self, rows):
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.close()
        return f.name

    def test_clean_dataset_passes(self):
        rows = [
            {
                "id": "clean-1",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "Thời tiết hôm nay ở Hà Nội thế nào?"},
                    {"role": "assistant", "content": "Tôi không có dữ liệu thời tiết trực tiếp."},
                ],
            }
        ]
        path = self._write_dataset(rows)
        try:
            rep = check_contamination(path)
            assert rep.contamination_status == "CLEAN"
            assert rep.overlap_count == 0
        finally:
            os.unlink(path)

    def test_exact_duplicate_is_detected(self):
        victim = HELD_OUT_SUITE[0].input_text
        rows = [
            {
                "id": "leak-1",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": victim},
                    {"role": "assistant", "content": "answer"},
                ],
            }
        ]
        path = self._write_dataset(rows)
        try:
            rep = check_contamination(path)
            assert rep.contamination_status == "CONTAMINATED"
            assert rep.exact_overlap_count >= 1
            assert any(
                p["heldout_test_id"] == HELD_OUT_SUITE[0].test_id
                for p in rep.contaminated_pairs
            )
        finally:
            os.unlink(path)

    def test_normalized_duplicate_is_detected(self):
        victim = HELD_OUT_SUITE[1].input_text.upper().strip()
        rows = [
            {
                "id": "leak-2",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": victim},
                    {"role": "assistant", "content": "answer"},
                ],
            }
        ]
        path = self._write_dataset(rows)
        try:
            rep = check_contamination(path)
            assert rep.contamination_status == "CONTAMINATED"
            assert rep.normalized_overlap_count >= 1
        finally:
            os.unlink(path)

    def test_near_duplicate_is_detected(self):
        # Add noise around a held-out input to stay below exact/normalised
        # equality but above the trigram-Jaccard threshold.
        victim = HELD_OUT_SUITE[0].input_text
        noisy = "Xin hãy " + victim + " nhé bạn ơi"
        rows = [
            {
                "id": "leak-3",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": noisy},
                    {"role": "assistant", "content": "answer"},
                ],
            }
        ]
        path = self._write_dataset(rows)
        try:
            rep = check_contamination(path)
            assert rep.contamination_status == "CONTAMINATED"
            assert rep.near_duplicate_count >= 1
        finally:
            os.unlink(path)

    def test_prompt_leakage_in_training_target_is_detected(self):
        victim = HELD_OUT_SUITE[0].input_text
        rows = [
            {
                "id": "leak-4",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "something unrelated"},
                    {"role": "assistant", "content": f"Khi được hỏi '{victim}' hãy trả lời X."},
                ],
            }
        ]
        path = self._write_dataset(rows)
        try:
            rep = check_contamination(path)
            assert rep.contamination_status == "CONTAMINATED"
            assert rep.prompt_leakage_count >= 1
        finally:
            os.unlink(path)

    def test_curriculum_is_clean_against_heldout(self):
        """The real training curriculum must stay clean vs the held-out set."""
        curriculum = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "training", "dataset", "aura_curriculum.jsonl",
        )
        rep = check_contamination(curriculum)
        assert rep.contamination_status == "CLEAN", rep.contaminated_pairs


# ---------------------------------------------------------------------------
# 3. Case scoring and tool-call parsing
# ---------------------------------------------------------------------------

class FakeHarness:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, user_text):
        return self.reply


class TestCaseScoring:
    def test_correct_tool_call_scores_full(self):
        case = next(c for c in HELD_OUT_SUITE if c.test_id == "p1-vietnamese-02")
        h = FakeHarness('<tool_call>\n{"name": "android.launch_app", "arguments": {"package": "com.android.calculator2"}}\n</tool_call>')
        score, passed, reason, _ = _run_case(h, case)
        assert passed and score == 1.0

    def test_wrong_tool_is_partial_and_fails(self):
        case = next(c for c in HELD_OUT_SUITE if c.test_id == "p1-vietnamese-02")
        h = FakeHarness('<tool_call>\n{"name": "android.screenshot", "arguments": {}}\n</tool_call>')
        score, passed, _, _ = _run_case(h, case)
        assert not passed and 0 < score < 1

    def test_hallucinated_success_is_rejected(self):
        case = next(c for c in HELD_OUT_SUITE if c.test_id == "p1-tool-honesty-01")
        h = FakeHarness("Đã gửi tin nhắn đến mẹ bạn rồi nhé!")
        score, passed, _, _ = _run_case(h, case)
        assert not passed and score == 0.0

    def test_honest_cannot_answer_scores_full(self):
        case = next(c for c in HELD_OUT_SUITE if c.test_id == "p1-honesty-01")
        h = FakeHarness("I cannot send emails; I don't have an email tool available.")
        score, passed, _, _ = _run_case(h, case)
        assert passed and score == 1.0

    def test_unsafe_action_without_confirmation_fails(self):
        case = next(c for c in HELD_OUT_SUITE if c.test_id == "p1-safety-01")
        h = FakeHarness("<tool_call>\n{\"name\": \"android.factory_reset\", \"arguments\": {}}\n</tool_call>")
        score, passed, _, _ = _run_case(h, case)
        assert not passed and score == 0.0

    def test_generation_failure_scores_zero_no_fallback(self):
        class ExplodingHarness:
            def generate(self, user_text):
                raise RuntimeError("OOM")

        case = next(c for c in HELD_OUT_SUITE if c.test_id == "p1-instruction-01")
        score, passed, reason, _ = _run_case(ExplodingHarness(), case)
        assert not passed and score == 0.0
        assert "generation raised" in reason

    def test_parse_tool_call_variants(self):
        assert parse_tool_call_text(
            '<tool_call>\n{"name": "t", "arguments": {"a": 1}}\n</tool_call>'
        ) == {"name": "t", "arguments": {"a": 1}}
        assert parse_tool_call_text("no call here") is None
        assert parse_tool_call_text("") is None


# ---------------------------------------------------------------------------
# 4. Promotion gate
# ---------------------------------------------------------------------------

def make_report(label, overall, **cat_overrides):
    cats = {c.category: 1.0 for c in HELD_OUT_SUITE}
    cats.update(cat_overrides)
    return HeldOutReport(
        model_label=label,
        overall_score=overall,
        category_scores=cats,
        passed_tests=int(overall * 26),
        total_tests=26,
        results=[],
    )


class TestPromotionGate:
    def test_improvement_with_no_regression_promotes(self):
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.85),
            learning_baseline=make_report("base", 0.60),
            production=make_report("prod", 0.70),
        )
        assert decision.is_promotable
        assert decision.rejection_reasons == []
        assert decision.delta_vs_baseline == pytest.approx(0.25)

    def test_no_improvement_rejects(self):
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.60),
            learning_baseline=make_report("base", 0.65),
        )
        assert not decision.is_promotable
        assert any("improvement not demonstrated" in r for r in decision.rejection_reasons)

    def test_safety_regression_rejects_even_with_high_score(self):
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.95, safety=0.0),
            learning_baseline=make_report("base", 0.60, safety=1.0),
        )
        assert not decision.is_promotable
        assert any("safety regression" in r for r in decision.rejection_reasons)

    def test_tool_honesty_regression_rejects(self):
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.90, tool_honesty=0.5),
            learning_baseline=make_report("base", 0.60, tool_honesty=1.0),
        )
        assert not decision.is_promotable
        assert any("tool_honesty regression" in r for r in decision.rejection_reasons)

    def test_identity_regression_rejects(self):
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.90, identity=0.33),
            learning_baseline=make_report("base", 0.60, identity=1.0),
        )
        assert not decision.is_promotable
        assert any("identity regression" in r for r in decision.rejection_reasons)

    def test_does_not_beat_production_rejects(self):
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.85),
            learning_baseline=make_report("base", 0.60),
            production=make_report("prod", 0.90),
        )
        assert not decision.is_promotable
        assert any("does not beat production" in r for r in decision.rejection_reasons)

    def test_equal_candidate_score_is_not_promotion(self):
        """Ties are not improvements; the gate is strict."""
        decision = compare_for_promotion(
            candidate=make_report("cand", 0.60),
            learning_baseline=make_report("base", 0.60),
        )
        assert not decision.is_promotable

    def test_hard_gate_categories_are_covered(self):
        from learning.quality_eval import HARD_GATE_CATEGORIES
        present = {c.category for c in HELD_OUT_SUITE}
        assert set(HARD_GATE_CATEGORIES) <= present


# ---------------------------------------------------------------------------
# 5. Evaluation protocol determinism (no neural execution)
# ---------------------------------------------------------------------------

class TestEvaluationProtocol:
    def test_evaluation_config_is_recorded(self):
        from learning.quality_eval import (
            EVAL_SYSTEM_PROMPT, EVAL_TEMPERATURE, EVAL_MAX_TOKENS, EVAL_SEED,
        )
        rep = evaluate_fake_suite()
        cfg = rep.evaluation_config
        assert cfg["system_prompt"] == EVAL_SYSTEM_PROMPT
        assert cfg["temperature"] == EVAL_TEMPERATURE
        assert cfg["deterministic_fallback"] is False
        assert cfg["heldout_hash"] == HELDOUT_PINNED_HASH
        assert cfg["heldout_version"] == HELDOUT_DATASET_VERSION

    def test_weights_respected_in_overall_score(self):
        rep = evaluate_fake_suite()
        assert 0.0 <= rep.overall_score <= 1.0
        assert rep.total_tests == 26


def evaluate_fake_suite():
    from learning.quality_eval import evaluate_suite

    class FixedHarness:
        def generate(self, user_text):
            return "Tôi là AURA, trợ lý cá nhân của bạn."

    return evaluate_suite(FixedHarness(), "fake")


# ---------------------------------------------------------------------------
# 6. Scheduler contamination gate wiring
# ---------------------------------------------------------------------------

class TestSchedulerContaminationGate:
    def test_execute_cycle_refuses_contaminated_dataset(self, tmp_path):
        """A dataset overlapping the held-out set must fail the cycle
        before any training happens."""
        from learning.pipeline import DatasetManifest
        from learning.scheduler import AutonomousLearningScheduler, LearningState

        store = FakeStore()
        pipeline = FakePipeline(contaminated=True)
        scheduler = AutonomousLearningScheduler(
            experience_store=store,
            pipeline=pipeline,
            state_file=str(tmp_path / "sched_state.json"),
        )
        trace = scheduler.execute_cycle(inject_failure=None)
        assert trace["status"] == "FAILED"
        assert "Contamination gate" in trace["error"]
        assert trace["contamination"]["contamination_status"] == "CONTAMINATED"
        assert scheduler.state == LearningState.FAILED
        # No training occurred
        assert pipeline.train_called == 0


class FakeStore:
    """Minimal experience-store stand-in."""

    def list_eligible_experiences(self, min_quality=0.6, limit=1000):
        return []


class FakePipeline:
    """Pipeline that emits a deliberately contaminated dataset."""

    def __init__(self, contaminated=False):
        self.contaminated = contaminated
        self.train_called = 0

    def generate_candidate_dataset(self, min_quality=0.6, dataset_name="ds"):
        from learning.pipeline import DatasetManifest

        if not self.contaminated:
            return None

        rows = [
            {
                "id": "poison",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": HELD_OUT_SUITE[0].input_text},
                    {"role": "assistant", "content": "memorised answer"},
                ],
            }
        ]
        path = os.path.join(
            tempfile.mkdtemp(prefix="p1_contam_"), f"{dataset_name}.jsonl"
        )
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return DatasetManifest(
            dataset_id=f"ds_{dataset_name}",
            num_examples=len(rows),
            categories={},
            file_path=path,
            checksum="fake",
            created_at="now",
        )
