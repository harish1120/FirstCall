from pathlib import Path

import yaml

_protocols: dict = {}


def _load():
    global _protocols
    path = Path(__file__).parent / "first_aid.yaml"
    with open(path) as f:
        _protocols = yaml.safe_load(f)


def get_first_aid_protocol(description: str) -> str:
    if not _protocols:
        _load()

    text = description.lower()
    for condition, steps in _protocols.items():
        if any(kw in text for kw in steps.get("keywords", [])):
            return "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps["steps"]))

    return _protocols.get("default", {}).get("steps", ["Keep the person calm and still."])[0]
