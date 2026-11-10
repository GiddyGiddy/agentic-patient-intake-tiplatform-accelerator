import os

import pytest

from app.core import db
from app.core.epa_client import EPAReadResult
from app.core.session import store
from config import settings


@pytest.fixture(autouse=True)
def clean_db():
    # fresh in-memory SQLite per test; production uses PostgreSQL (same SQLAlchemy models)
    db.configure(os.environ.get("TEST_DATABASE_URL", "sqlite://"))
    db.Base.metadata.create_all(db._engine)
    store._hits.clear()
    yield
    db.Base.metadata.drop_all(db._engine)


@pytest.fixture(autouse=True)
def no_llm_no_auth(monkeypatch):
    """Tests never call a real LLM and skip token validation."""
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "auth_disabled", True)
    monkeypatch.setattr(
        "app.agents.consent_agent.fetch_patient_summary",
        lambda *args: EPAReadResult(
            medication_plan=["Ramipril 5 mg"],
            status="available",
        ),
    )
