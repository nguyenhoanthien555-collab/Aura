"""
Skill discovery: natural-language intent -> executable skill selection.

Discovery answers one question with evidence instead of vibes: given what
the user asked for in plain language, which registered capability is the
best match, and may it actually run right now?

Two stages, deliberately kept apart:

    ranking       semantic relevance only - words of the intent measured
                  against each capability's declared identity (description,
                  name, category, provider-registered keywords)
    selection     the LIVE capability registry decides. A candidate that
                  ranks first but is permission-blocked, unhealthy or has
                  no companion heartbeat never becomes "the selected
                  skill"; `select_best_executable` walks down the ranked
                  list until it finds a candidate whose state really is
                  AVAILABLE, and reports why the leaders were rejected.

The resolver never invents capabilities: everything it can return comes
from `registry.all()`, and every state it reports comes from
`resolve_capability` re-evaluating permissions and health on this call.
"""

import re
from typing import Any, Dict, List, Optional

from core.capabilities import permissions as _permissions
from core.capabilities import registry, resolve_capability
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger


# Words that can never carry meaning by themselves - scoring them let
# sentences like "recite poetry from a pinecone" collide with anything
# whose description contained "a". Tokens are letters-only runs.
_TOKEN_RE = re.compile(r"[a-z]+")
_STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "into", "onto", "about", "between", "through", "during",
    "before", "after", "above", "below", "up", "down", "out", "off",
    "over", "under", "again", "further", "once", "here", "there", "now",
    "and", "or", "nor", "but", "so", "as", "if", "then", "than",
    "it", "its", "itself", "they", "them", "their", "he", "she", "his", "her",
    "you", "your", "yours", "i", "me", "my", "we", "our", "us",
    "do", "did", "done", "when", "while", "just", "also", "very",
    "some", "any", "all", "both", "each", "few", "other", "such",
    "only", "own", "same", "can", "cannot", "could", "should", "would",
    "will", "shall", "may", "might", "must",
    "what", "which", "who", "whom", "whose", "why", "how",
})


class SkillDiscovery:

    # Capabilities whose entire purpose is reporting current device state.
    # Question-shaped intents are answered from these or not at all.
    OBSERVATION_CAPABILITIES = frozenset({
        "android.foreground_app",
        "android.ui_tree",
        "android.ui_search",
        "android.screen_capture",
    })

    # Intents phrased as questions demand observations. An action verb in
    # a question ("what app is open", "find the Settings button") does not
    # license a mutation, so side-effectful candidates rank below the
    # read-only way to answer.
    QUESTION_PATTERN = re.compile(
        r"\b(what|which|where|who|is|are|does|did|can|see|find|show|"
        r"check|look)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    @staticmethod
    def _tokens(text) -> set:
        """Meaningful lowercase word tokens of any text."""

        return {
            token
            for token in _TOKEN_RE.findall(str(text).lower())
            if len(token) > 1 and token not in _STOPWORDS
        }

    def _intent_words(self, intent: str) -> set:
        return self._tokens(intent)

    def _capability_words(self, cap: Capability) -> set:
        """The words a capability declares about itself."""

        words: set = set()
        for phrase in (
            cap.description,
            cap.name,
            cap.category,
            *(cap.discovery_metadata.get("keywords") or []),
        ):
            words.update(self._tokens(phrase))

        return words

    def _semantic_score(self, intent: str, cap: Capability) -> float:
        intent_words = self._intent_words(intent)
        target_words = self._capability_words(cap)

        overlap = intent_words.intersection(target_words)

        score = len(overlap) * 1.0

        # Boosts for specific keywords
        if "phone" in intent_words or "android" in intent_words or "app" in intent_words:
            if "android" in cap.category.lower():
                score += 1.0

        if "screen" in intent_words or "ui" in intent_words:
            if (
                "observation" in cap.capability_id.lower()
                or "capture" in cap.capability_id.lower()
                or cap.capability_id == "android.ui_tree"
            ):
                score += 1.0

        if "press" in intent_words or "tap" in intent_words or "click" in intent_words:
            if (
                "control" in cap.capability_id.lower()
                or cap.capability_id == "android.tap"
            ):
                score += 1.0

        if "enter" in intent_words or "type" in intent_words or "text" in intent_words:
            if (
                "control" in cap.capability_id.lower()
                or cap.capability_id in ("android.text_input", "android.key_input")
            ):
                score += 1.0

        if "home" in intent_words:
            if (
                "control" in cap.capability_id.lower()
                or cap.capability_id == "android.home"
            ):
                score += 1.0


        if "find" in intent_words or "button" in intent_words:
            if (
                "observation" in cap.capability_id.lower()
                or "search" in cap.capability_id.lower()
            ):
                score += 1.0

        if {"screenshot", "capture"} & intent_words:
            if cap.capability_id == "android.screen_capture":
                score += 2.0

        if {"open", "launch", "start"} & intent_words:
            if "launch" in cap.capability_id:
                score += 1.5

        if "foreground" in intent_words and "foreground" in cap.capability_id:
            score += 2.0

        # Question intents want current state, not changes. Everything the
        # user asked for can be answered by an observation; reaching for a
        # mutating tool to "answer" would mean acting where reading was meant.
        if self.QUESTION_PATTERN.search(intent):
            if cap.capability_id in self.OBSERVATION_CAPABILITIES:
                score += 2.5
            elif cap.side_effects:
                score -= 2.0

        return score

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, intent: str, threshold: float = 0.5) -> List[Dict[str, Any]]:
        """Ranked candidates for an intent, with their LIVE state attached."""

        candidates = []
        for cap in registry.all():
            score = self._semantic_score(intent, cap)
            if score >= threshold:
                candidates.append((score, cap))

        # Deterministic order: score descending, then id, so equal scores
        # cannot flip between calls and make transcripts unreproducible.
        candidates.sort(key=lambda pair: (-pair[0], pair[1].capability_id))

        results = []
        for score, cap in candidates:
            state = resolve_capability(cap.capability_id)
            results.append({
                "capability_id": cap.capability_id,
                "name": cap.name,
                "score": round(score, 3),
                "state": state.value,
                "reason": cap.discovery_metadata.get("state_reason", ""),
                "tool": cap.discovery_metadata.get("tool", ""),
            })

        return results

    def select_best_executable(
        self,
        intent: str,
        threshold: float = 0.5,
    ) -> Optional[Dict[str, Any]]:
        """
        The best-ranked candidate whose live state is actually AVAILABLE.

        Ranking proposes, the registry disposes: a top-ranked capability
        that is BLOCKED_PERMISSION / UNHEALTHY / UNAVAILABLE is skipped so
        the call site never executes against dead runtime state. Returns
        None when nothing matched, or when every match is currently
        non-executable - never a blocked capability dressed up as usable.
        """

        ranked = self.discover(intent, threshold=threshold)

        for item in ranked:
            if item["state"] == CapabilityState.AVAILABLE.value:
                return item

        return None

    def explain(self, intent: str, threshold: float = 0.5) -> Dict[str, Any]:
        """
        Ranked candidates plus a diagnosis when no executable one exists.

        The failure report distinguishes "nothing semantically matched"
        from "matched but cannot run right now (state + reason)", so the
        caller can tell the user exactly which capability exists, whether
        it is authorized, healthy, and what precisely blocks it.
        """

        ranked = self.discover(intent, threshold=threshold)
        selected = next(
            (item for item in ranked if item["state"] == CapabilityState.AVAILABLE.value),
            None,
        )

        diagnosis: Dict[str, Any] = {
            "no_capability_matched": not ranked,
            "all_candidates_blocked": bool(ranked) and selected is None,
            "missing_permissions": [],
            "unhealthy_dependencies": [],
        }

        for item in ranked:
            cap = registry.get(item["capability_id"])
            if cap is None:
                continue
            if item["state"] == CapabilityState.BLOCKED_PERMISSION.value:
                missing_details = [
                    {"permission": permission, "reason": reason}
                    for permission, reason in _permissions.missing_details(
                        cap.required_permissions
                    )
                ]
                diagnosis["missing_permissions"].append({
                    "capability_id": item["capability_id"],
                    "permissions": list(cap.required_permissions),
                    "reason": item["reason"],
                    "missing": missing_details,
                })
            elif item["state"] in (
                CapabilityState.UNHEALTHY.value,
                CapabilityState.UNAVAILABLE.value,
            ):
                diagnosis["unhealthy_dependencies"].append({
                    "capability_id": item["capability_id"],
                    "state": item["state"],
                    "reason": item["reason"],
                })

        if ranked:
            leader = ranked[0]
            diagnosis["top_candidate"] = {
                "capability_id": leader["capability_id"],
                "state": leader["state"],
                "reason": leader["reason"],
                "score": leader["score"],
            }

        return {
            "intent": intent,
            "ranked": ranked,
            "selected": selected,
            "diagnosis": diagnosis,
        }


discovery = SkillDiscovery()