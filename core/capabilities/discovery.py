import re
from typing import List, Dict, Any, Optional
from core.capabilities import registry, resolve_capability
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger

class SkillDiscovery:
    """
    Actual runtime skill/capability discovery.
    
    Given a user intent (string), it ranks capabilities semantically.
    """
    
    def __init__(self):
        pass
        
    def _semantic_score(self, intent: str, cap: Capability) -> float:
        # Keep ranking deterministic and ignore stop words so a shared
        # article cannot make unrelated capabilities tie.
        stop_words = {
            "a", "an", "and", "am", "for", "in", "into", "is", "it",
            "me", "my", "of", "on", "the", "to", "what", "which",
        }

        def words(text):
            return {
                word for word in re.findall(r"[a-z0-9_]+", text.lower())
                if word not in stop_words
            }
        intent_words = words(intent)
        desc_words = words(cap.description)
        name_words = words(cap.name)
        category_words = words(cap.category)
        metadata_words = words(" ".join(
            str(value) for value in cap.discovery_metadata.get("keywords", [])
        ))
        
        target_words = desc_words.union(name_words).union(category_words).union(metadata_words)
        
        overlap = intent_words.intersection(target_words)
        
        # Base score is overlap
        score = len(overlap) * 1.0
        
        # Boost if category matches exactly some keyword
        for w in intent_words:
            if w == cap.category.lower():
                score += 2.0

        # Action terms carry more signal than generic UI nouns. This resolves
        # legitimate ties such as `find` vs `type text`.
        boosts = {
            "android.ui_search": {"find", "search"},
            "android.tap": {"tap", "click", "button"},
            "android.text_input": {"type", "input", "text"},
            "android.key_input": {"key", "backspace", "delete"},
            "android.back": {"back"},
            "android.home": {"home", "launcher"},
            "android.app_launch": {"launch", "open", "start"},
            "android.screen_capture": {"observe", "capture", "screenshot"},
        }
        score += 2.0 * len(intent_words.intersection(
            boosts.get(cap.capability_id, set())
        ))
                
        return score

    def discover(self, intent: str, threshold: float = 0.5) -> List[Dict[str, Any]]:
        candidates = []
        for cap in registry.all():
            score = self._semantic_score(intent, cap)
            if score >= threshold:
                candidates.append((score, cap))
                
        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        results = []
        for score, cap in candidates:
            state = resolve_capability(cap.capability_id)
            results.append({
                "capability_id": cap.capability_id,
                "name": cap.name,
                "score": score,
                "state": state.value,
                "reason": cap.discovery_metadata.get("state_reason", "")
            })
            
        return results
        
    def select_best_executable(self, intent: str) -> Optional[Dict[str, Any]]:
        """
        Select the best capability that is actually AVAILABLE.
        A blocked semantic match is retained in `discover()` for an honest
        explanation, but is never returned as executable.
        """
        discovered = self.discover(intent)
        return next(
            (candidate for candidate in discovered
             if candidate["state"] == CapabilityState.AVAILABLE.value),
            None,
        )

discovery = SkillDiscovery()
