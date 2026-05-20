import re
from enum import StrEnum


class Severity(StrEnum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


CRITICAL_KEYWORDS = [
    "not breathing",
    "cardiac arrest",
    "heart attack",
    "choking",
    "can't breathe",
    "severe bleeding",
    "won't stop bleeding",
    "stroke",
    "face drooping",
    "anaphylaxis",
    "throat closing",
    "allergic reaction",
    "seizure",
    "convulsing",
    "poisoning",
    "overdose",
    "drowning",
    "unresponsive",
    "unconscious",
    "difficulty breathing",
    "not responsive",
    "turned blue",
    "stopped breathing",
    "heart stopped",
    "can't breathe",
    "not waking up",
    "collapsed",
]

URGENT_KEYWORDS = [
    "broken bone",
    "fracture",
    "deep cut",
    "laceration",
    "moderate burn",
    "head injury",
    "fell",
    "high fever",
    "chest pain",
    "hit by",
    "car accident",
    "accident",
    "fell",
    "knocked out",
    "unconscious",
    "trauma",
    "hit by car",
    "knocked unconscious",
    "can't move",
    "head wound",
    "deep wound",
    "won't stop",
    "heavy bleeding",
]


def strip_negations(text: str) -> str:
    return re.sub(r"\b(no|not|isn't|wasn't|never|without)\b.{0,25}", "", text)


def triage_severity(description: str) -> Severity:
    """Rule-based triage gate. HITL — not delegated to the LLM."""
    raw = description.lower()
    if any(kw in raw for kw in CRITICAL_KEYWORDS):
        return Severity.CRITICAL
    text = strip_negations(raw)
    if any(kw in text for kw in URGENT_KEYWORDS):
        return Severity.URGENT
    return Severity.ROUTINE


def get_emergency_number(country_code: str) -> str:
    numbers = {
        "US": "911",
        "CA": "911",
        "GB": "999",
        "AU": "000",
        "DEFAULT": "112",
    }
    return numbers.get(country_code.upper(), numbers["DEFAULT"])
