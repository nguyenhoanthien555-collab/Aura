import math
from typing import List, Dict, Any, Optional
from core.capabilities import registry, resolve_capability
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger

class SkillDiscovery:
    def __init__(self):
        pass
        
    def _semantic_score(self, intent: str, cap: Capability) -> float:
        intent_words = set(intent.lower().replace('?','').replace('.','').split())
        desc_words = set(cap.description.lower().replace('.','').split())
        name_words = set(cap.name.lower().replace('.','').split())
        category_words = set(cap.category.lower().split())
        
        target_words = desc_words.union(name_words).union(category_words)
        
        overlap = intent_words.intersection(target_words)
        
        score = len(overlap) * 1.0
        
        # Boosts for specific keywords
        if "phone" in intent_words or "android" in intent_words or "app" in intent_words:
            if "android" in cap.category.lower():
                score += 1.0
        
        if "screen" in intent_words or "ui" in intent_words:
            if "observation" in cap.capability_id.lower() or "capture" in cap.capability_id.lower():
                score += 1.0
                
        if "press" in intent_words or "tap" in intent_words or "click" in intent_words:
            if "control" in cap.capability_id.lower():
                score += 1.0
                
        if "enter" in intent_words or "type" in intent_words or "text" in intent_words:
            if "control" in cap.capability_id.lower():
                score += 1.0
                
        if "home" in intent_words:
            if "control" in cap.capability_id.lower():
                score += 1.0

        if "find" in intent_words or "button" in intent_words:
            if "observation" in cap.capability_id.lower() or "control" in cap.capability_id.lower():
                score += 1.0
                
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
        discovered = self.discover(intent)
        if not discovered:
            return None
        return discovered[0]

discovery = SkillDiscovery()
