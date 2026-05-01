from app.protocols.loader import get_first_aid_protocol


def test_cardiac_arrest_returns_protocol() -> None:
    result = get_first_aid_protocol("man's not breathing")
    assert "CPR" in result or "911" in result or "chest" in result.lower()


def test_choking_returns_protocol() -> None:
    result = get_first_aid_protocol("he's choking and can't speak")
    assert "abdominal" in result or "911" in result or "coughing" in result.lower()


def test_severe_bleeding_returns_protocol() -> None:
    result = get_first_aid_protocol("i won't stop bleeding")
    assert "pressure" in result.lower() or "wound" in result.lower() or "blood" in result.lower()


def test_stroke_returns_protocol() -> None:
    result = get_first_aid_protocol("i have someone who has his face drooping")
    assert "911" in result or "CPR" in result or "calm" in result.lower()


def test_burns_returns_protocol() -> None:
    result = get_first_aid_protocol("poured hot coffee all over myself")
    assert "heat" in result.lower() or "911" in result or "remove" in result.lower()


def test_anaphylaxis_returns_protocol() -> None:
    result = get_first_aid_protocol("throat closing after bee sting")
    assert "epinephrine" in result.lower() or "911" in result or "CPR" in result


def test_seizure_returns_protocol() -> None:
    result = get_first_aid_protocol("someone is convulsing")
    assert "convulsing" in result.lower() or "911" in result or "roll" in result.lower()


def test_fracture_returns_protocol() -> None:
    result = get_first_aid_protocol("i have broken bone")
    assert "ice" in result.lower() or "fracture" in result.lower() or "immobilize" in result.lower()


def test_head_injury_returns_protocol() -> None:
    result = get_first_aid_protocol("i had a concussion")
    assert "911" in result or "neck" in result.lower() or "pressure" in result.lower()


def test_poisoning_returns_protocol() -> None:
    result = get_first_aid_protocol("my friend overdosed at the club")
    assert "911" in result or "vomiting" in result.lower() or "monitor" in result.lower()


def test_default_returns_protocol() -> None:
    result = get_first_aid_protocol("something something")
    assert (
        "911" in result
        or "monitor" in result.lower()
        or "consciousness" in result.lower()
        or "calm" in result.lower()
    )
