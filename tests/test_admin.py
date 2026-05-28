import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import CallLog
from app.triage import Severity

TEST_DB_URL = "sqlite:///./test_admin.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_admin_calls_requires_auth(client):
    response = client.get("/admin/calls")
    assert response.status_code == 401


def test_admin_calls_returns_empty_list(client):
    response = client.get("/admin/calls", auth=("admin", ""))
    assert response.status_code == 200
    assert response.json() == []


def test_admin_calls_returns_call_logs(client, db):
    db.add(
        CallLog(
            call_sid="CA123",
            severity=Severity.CRITICAL,
            condition="Cardiac arrest",
            duration_seconds=120,
            summary="Called 911, started CPR",
            steps_completed=3,
            called_911=True,
            avg_latency_ms=487.5,
            avg_ttft_ms=312.0,
        )
    )
    db.commit()

    response = client.get("/admin/calls", auth=("admin", ""))
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["condition"] == "Cardiac arrest"
    assert data[0]["severity"] == "CRITICAL"
    assert data[0]["avg_latency_ms"] == 487.5
    assert data[0]["avg_ttft_ms"] == 312.0
    assert data[0]["called_911"] is True


def test_admin_calls_returns_newest_first(client, db):
    for i, sid in enumerate(["CA001", "CA002", "CA003"]):
        db.add(
            CallLog(
                call_sid=sid,
                severity=Severity.ROUTINE,
                condition=f"Condition {i}",
                duration_seconds=60,
            )
        )
    db.commit()

    response = client.get("/admin/calls", auth=("admin", ""))
    sids = [r["call_sid"] for r in response.json()]
    assert sids == ["CA003", "CA002", "CA001"]
