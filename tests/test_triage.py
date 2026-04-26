from app.triage import Severity, get_emergency_number, triage_severity


def test_cardiac_arrest_is_critical():
    assert triage_severity("my dad is unresponsive and not breathing") == Severity.CRITICAL


def test_choking_is_critical():
    assert triage_severity("my kid is choking and turning blue") == Severity.CRITICAL


def test_fracture_is_urgent():
    assert triage_severity("I think I have a broken bone in my arm") == Severity.URGENT


def test_minor_cut_is_routine():
    assert triage_severity("I have a small cut on my finger") == Severity.ROUTINE


def test_emergency_numbers():
    assert get_emergency_number("US") == "911"
    assert get_emergency_number("GB") == "999"
    assert get_emergency_number("AU") == "000"
    assert get_emergency_number("XX") == "112"
