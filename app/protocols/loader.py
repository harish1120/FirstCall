from pathlib import Path
from typing import Any

import yaml

_protocols: dict[str, Any] = {}


def _load() -> None:
    global _protocols
    path = Path(__file__).parent / "first_aid.yaml"
    with open(path) as f:
        _protocols = yaml.safe_load(f)


def get_first_aid_protocol(description: str) -> str:
    if not _protocols:
        _load()

    text = description.lower()
    for _condition, steps in _protocols.items():
        if any(kw in text for kw in steps.get("keywords", [])):
            return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps["steps"]))

    return str(_protocols.get("default", {}).get("steps", ["Keep the person calm and still."])[0])
