from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    oidc_issuer: str = "http://mock-id-dienst:8081"
    oidc_jwks_url: str = "http://mock-id-dienst:8081/jwks"
    oidc_audience: str = "patient-intake"
    auth_disabled: bool = False

    fhir_base_url: str = "http://mock-kis-fhir:8080/fhir"
    database_url: str = "postgresql+psycopg://intake:intake@postgres:5432/intake"
    # Dev/tests: create tables on start-up. Production sets this to false and runs `alembic upgrade head`.
    db_auto_create: bool = True
    kim_retry_interval_s: int = 60
    kim_max_attempts: int = 10
    # Dev: the same HAPI mock serves KIS and ePA. Prod: TI gateway/Konnektor URL of the ePA FHIR API.
    epa_base_url: str = "http://mock-kis-fhir:8080/fhir"
    epa_timeout_s: float = 10.0
    # KIS/TI gateway calls of the scheduling agent: (connect, read) seconds; a blocked HSM/SMC-B must not hang the run
    fhir_connect_timeout_s: float = 2.0
    fhir_read_timeout_s: float = 8.0

    kim_smtp_host: str = "mock-kim-cm"
    kim_smtp_port: int = 2525
    kim_sender: str = "intake@praxis.kim.test"
    kim_recipient: str = "arzt@praxis.kim.test"

    llm_base_url: str = ""
    llm_model: str = "llama3.2:3b"
    llm_provider: Literal["ollama", "foundry_local"] = "ollama"

    cors_origins: str = "http://localhost:3000"  # comma separated
    session_ttl_s: int = 3600  # idle lifetime of an intake session
    consent_ttl_s: int = 3600  # lifetime of a granted TI-access consent
    max_session_messages: int = 30
    max_message_chars: int = 2000
    rate_limit_per_min: int = 30
    audit_log_file: str = ""  # empty = stdout only


settings = Settings()
