# Agentic Patient Intake 

Agentic AI patient intake on the Telematikinfrastruktur (TI): structured anamnesis, Manchester triage, ePA consent, FHIR appointment booking in the KIS and a KIM letter to the referring doctor. Built with React, FastAPI, LangGraph, PostgreSQL and a local LLM. Runs **on-premise** with Podman; the TI services(ti-gateway, KIM-CM,Id-services) are simulated by local mocks for development and end-to-end tests.

## Current status

| Area | Status |
|---|---|
| Frontend (OIDC PKCE login, chat, consent dialog, emergency banner, result card, a11y) | Done, 26 Jest tests |
| Backend (agents, server-side session and consent, ePA read, FHIR booking, KIM, audit, hardening, ErrorHandler for TI failures) | Done, 66 pytest tests |
| PostgreSQL persistence (sessions, consent, audit, KIM outbox) | Done, tests pass on SQLite and on a real PostgreSQL; Alembic migrations for production |
| Mock TI services (ID-Dienst, KIS/ePA HAPI FHIR, KIM client module, TI gateway with simulated SMC-B) | Done |
| CI (GitHub Actions: tests on SQLite + PostgreSQL, frontend build, pip/npm audit, gitleaks, image builds + Trivy) | Done, green on GitHub |
| CD to the on-premise server (`cd.yml`, `compose.prod.yml`, `deploy/`) | Pull-based: images built, scanned and pushed to GHCR by GitHub; server runs `deploy/deploy.sh`. Production image stack verified locally; not yet run on the VM |
| Podman compose for the whole dev stack | Config validated; full `podman compose up --build` not yet run end-to-end |
| Browser run-through of frontend + backend + mocks together | Not yet done |
| Production TI connection (real gateway/Konnektor, SMC-B/HSM-B, real Gesundheits-ID) | Not part of this accelerator; only URLs/config change |

## Solution Architecture

![Agentic Patient Intake on-premise solution architecture](./docs/solution-architecture.svg)

The backend, database and LLM run in the clinic's own data center because the data is sensitive health data. Nothing is sent to a public cloud. The Azure items in the diagram (Arc, Monitor) are an optional management layer for operational metadata only. The icons are simplified drawings, not the official Azure icon set.

### Components

| Component | Technology | Where | Dev mock |
|---|---|---|---|
| Frontend | React 18, nginx | On-premise | – |
| Backend | FastAPI, LangGraph, SQLAlchemy | On-premise | – |
| Database | PostgreSQL 16 (only persistent store) | On-premise | – |
| LLM | Ollama or Foundry Local (local, rule-based fallback) | On-premise | – |
| Gesundheits-ID (OIDC) | OpenID Connect / OAuth2 | TI | `mock-id-dienst` :8081 |
| TI gateway / Konnektor (HSM-B / SMC-B) | Reverse proxy, signs requests, adds Telematik-ID | TI | `mock-ti-gateway` :8082 |
| ePA + KIS (FHIR / ISiK) | HAPI FHIR | TI / clinic | `mock-kis-fhir` :8080 (+ `mock-kis-seed`) |
| KIM client module | SMTP | TI | `mock-kim-cm` :2525 |

## Start

```powershell
podman compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend (OpenAPI) | http://localhost:8000/docs |
| Mock ID-Dienst | http://localhost:8081/.well-known/openid-configuration |
| Mock TI gateway | http://localhost:8082/health |
| Mock KIS/ePA (HAPI FHIR) | http://localhost:8080/fhir |
| Mock KIM-CM (SMTP) | localhost:2525 |
| LLM (Ollama) | http://localhost:11434 (`podman compose exec llm ollama pull llama3.2:3b`) |

Demo patients: `X110000001` (two active medications in the ePA), `X110000002` (none). Without a reachable LLM the intake agent uses a rule-based fallback. On Windows, use `127.0.0.1` instead of `localhost` for published database ports.

The development `.env` selects `llama3.2:3b` (Compose falls back to `llama3.1` if
`LLM_MODEL` is unset). To evaluate another locally installed
Ollama model, set `LLM_MODEL` in the shell before starting or recreating the backend; in
PowerShell, for example, use `$env:LLM_MODEL = "<model-name>"` and then
`podman compose up -d --no-build backend`. Changing the shell variable does not update an
already running container. Model output and triage must be evaluated using synthetic data;
neither the model nor the rule-based fallback is validated for clinical use.

### Compare local models with synthetic data

The backend supports `LLM_PROVIDER=ollama` (default) and
`LLM_PROVIDER=foundry_local`. Both intake and KIM use the selected provider.
The benchmark sends two fixed synthetic intake prompts to the same adapter and
reports elapsed time, the response, and whether its JSON has the expected fields.
It is **not** an accuracy, hallucination, safety, or clinical validation. Compare
the outputs manually as well as the timings; the first request may include
model warm-up. Never use real patient information in this benchmark.

For Foundry Local on Windows, install Foundry Local and the backend's Python
dependencies if not already present. The following uses the NPU variant of
Phi-4 Mini; select a device/model supported by your hardware and available
memory. Downloading and loading may take time:

```powershell
foundry model download phi-4-mini --device NPU
foundry model load phi-4-mini --device NPU
foundry service status
foundry service list
```

Use the **actual** service origin printed by `foundry service status` and the
loaded **Model ID** from `foundry service list`; the port and model variant can
differ from these examples. From the repository root:

```powershell
python backend\benchmark_llm.py --provider foundry_local --base-url http://127.0.0.1:5273 --model phi-4-mini-instruct-openvino-npu:4 --repeats 2
python backend\benchmark_llm.py --provider ollama --base-url http://127.0.0.1:11434 --model qwen2.5:3b --repeats 2
```

Each model must first be downloaded and loaded by its corresponding runtime.
Repeat the Foundry commands with other catalog models and their supported device
options to compare them. A nonzero benchmark exit code indicates a request
failure or invalid response schema. For Python dependencies, use the same
environment as the backend (`python -m pip install -r backend\requirements.txt`).
If a separate host Ollama process already uses port 11434, the host benchmark
may reach that instance rather than the Compose container. Verify its model list
with `ollama list` and use the intended instance's reachable URL.

**Container networking:** A Windows Foundry endpoint bound to `127.0.0.1` is
reachable by the host benchmark, but that address inside the backend container
is the container itself. Do not expose Foundry publicly or on the LAN to work
around this. In-app Foundry use requires an independently configured, secured
endpoint reachable from the backend container; this connection has not been
verified for the Windows/Podman setup. Use synthetic data when testing it.
Once such an endpoint exists, set the following in PowerShell, replacing the
placeholders, then rebuild/recreate the development backend:

```powershell
$env:LLM_PROVIDER = "foundry_local"
$env:LLM_BASE_URL = "http://<container-reachable-host>:<port>"
$env:LLM_MODEL = "<loaded-model-id>"
podman compose up -d --build backend
```

To return to Ollama, remove these shell overrides and recreate the backend:

```powershell
Remove-Item Env:LLM_PROVIDER, Env:LLM_BASE_URL, Env:LLM_MODEL -ErrorAction SilentlyContinue
podman compose up -d --build backend
```

Compose then uses the values in `.env`, or its Ollama defaults if those entries
are absent. Production retains its existing Ollama configuration; do not change
it for a benchmark. Provider failures fall back to deterministic intake or a
template KIM letter, with a warning in the backend logs.

## Agent flow

```
POST /api/chat → intake_agent (anamnesis, Manchester triage)
      → consent_agent (stops until consent is granted)
      → scheduling_agent (FHIR Slot + Appointment in the KIS)
      → kim_agent (Arztbrief + triage.json → KIM outbox) → END
      scheduling failure (gateway/SMC-B error, timeout) → error_handler → END
```

- Consent, triage and conversation are held **server-side** per patient; the client cannot set them.
- The ePA medication plan is read only after consent, with the patient's Gesundheits-ID token.
- Red/orange patients get acute slots, green patients standard slots.
- KIS/gateway calls use a connect/read timeout (`FHIR_CONNECT_TIMEOUT_S`, `FHIR_READ_TIMEOUT_S`). Failures are mapped to `GATEWAY_TIMEOUT`, `GATEWAY_CONNECTION_FAILED`, `GATEWAY_AUTH_ERROR` or `GATEWAY_ERROR`; the `error_handler` node writes an alerting audit event (`scheduling_failed`), shows the patient a neutral German fallback message (no stack trace) and ends the run. The API response then has `scheduling_failed: true`.
- The graph is invoked in `backend/app/api/endpoints.py` (`_run`).
- For the request-by-request sequence, shared-state fields, and mock-service boundaries, see [docs/agent-sequence.md](docs/agent-sequence.md).

## Backend API

All endpoints require the Gesundheits-ID bearer token.

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` `{message}` | Adds a patient message and runs the graph. Asks follow-up questions until triage is set, then waits for consent. |
| `POST /api/consent` `{granted}` | Grant resumes the workflow: ePA medication plan, FHIR Appointment, KIM letter. Idempotent after completion. `granted:false` revokes. |
| `DELETE /api/session` | Start over / logout: drops conversation, triage and consent. |
| `GET /health` | Status including database reachability (503 if the DB is down). |

Errors: `502` TI service (KIS/ePA) unreachable (retry is safe), `409` intake already completed or none active, `422` message too long, `429` rate limit. A failed KIM hand-over does not fail the run; the response has `kim_sent: false` and the outbox retries.

## Persistence (PostgreSQL)

PostgreSQL is the only store (service `postgres`, volume `pg-data`). In dev and tests the tables are created on startup (`DB_AUTO_CREATE=true`). In production the schema is owned by **Alembic** (`DB_AUTO_CREATE=false`); `deploy/deploy.sh` backs up the database and runs `alembic upgrade head` before the new backend starts.

```powershell
cd backend
alembic revision --autogenerate -m "describe change" # after changing app/core/db.py
alembic upgrade head                                 # uses DATABASE_URL
```

A database created earlier by `create_all` needs a one-time `alembic stamp 0001`. Keep migrations backward compatible (add before remove), because a rollback restores the previous images, not the schema.

| Table | Content |
|---|---|
| `intake_session` | conversation, triage score, summary, result (per patient `sub`; idle sessions are purged) |
| `consent` | durable consent records (scope, granted/expires/revoked, token `jti`); never deleted |
| `audit_event` | pseudonymised audit events (no medical content); also written as JSON lines |
| `kim_outbox` | Arztbrief + `triage.json` until delivered; retried every `KIM_RETRY_INTERVAL_S`; content wiped after sending |

Not persisted: access tokens, ePA medication data, slots and appointments (they stay in the KIS/ePA).

## Mock TI gateway (SMC-B / HSM-B)

All KIS and ePA calls go through `mock-ti-gateway`: `FHIR_BASE_URL` and `EPA_BASE_URL` point to `http://mock-ti-gateway:8082/fhir`. The gateway keeps the patient's token, adds `X-TI-Telematik-ID` and an RS256 assertion (`X-SMCB-Assertion`, certificate in `x5c`) signed with a simulated SMC-B key. The self-signed key is generated at start-up or loaded from `certs/smcb_key.pem` + `smcb_cert.pem`; it is never committed. Follow it with `podman compose logs -f mock-ti-gateway`. The HAPI mock does not verify the assertion; the signature is covered by `backend/tests/test_ti_gateway.py`.

## Security and privacy

- Token validation against the Gesundheits-ID JWKS; `AUTH_DISABLED` is for local development only.
- Audit log uses a pseudonymised patient id (sha256 prefix) and contains no medical content.
- CORS allow-list, message length cap and per-patient rate limit.
- Consent is revoked on reset or session timeout.

## Development

```powershell
# backend
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                                   # in-memory SQLite
$env:TEST_DATABASE_URL="postgresql+psycopg://intake:intake@127.0.0.1:55432/intake"; python -m pytest -q   # real PostgreSQL
$env:AUTH_DISABLED="true"; uvicorn main:app --reload

# frontend
cd frontend
npm install
npm run dev      # http://localhost:3000
npm test         # Jest + Testing Library
npm run build
```

Frontend config is read at build time: `REACT_APP_API_URL`, `REACT_APP_OIDC_ISSUER`, `REACT_APP_OIDC_CLIENT_ID` (passed as build args in `docker-compose.yml`). See `.env` for all backend settings.

## CI/CD (on-premise production)

| Piece | Purpose |
|---|---|
| `.github/workflows/ci.yml` | On every PR and push to `main`: backend tests (SQLite + PostgreSQL), frontend tests and build, `pip-audit`, `npm audit` (critical), gitleaks, compose validation, image builds with Trivy (fixable HIGH/CRITICAL fail). |
| `.github/workflows/cd.yml` | After CI succeeds on `main`: build images on GitHub-hosted runners, tag with the 12-char commit SHA, Trivy scan, push to GHCR, SBOM, optional cosign signature. Deployment is pull-based: run `deploy/deploy.sh <env> <tag>` on the server. |
| `compose.prod.yml` | Production stack from registry images only: TLS proxy, frontend, backend, PostgreSQL, local LLM. No mocks, no published backend/DB ports, internal network without internet access. |
| `deploy/deploy.sh` | Pull, `pg_dump`, `alembic upgrade head`, `up -d`, health wait, **automatic rollback** to the previous tag. |
| `deploy/smoke-test.sh` | Black-box checks: health, frontend, HTTPS redirect, HSTS, auth enforced on the API. |
| `deploy/prod.env.example` | Template for the server-side secrets file `/etc/intake/<env>.env` (never committed). |

To enable deployments follow [docs/cd-setup.md](docs/cd-setup.md) (server bootstrap with `deploy/setup-server.sh`, GHCR pull login, TLS certificate, manual deploy and rollback). Optional repository variables `PROD_API_URL`, `PROD_OIDC_ISSUER`, `PROD_OIDC_CLIENT_ID` are baked into the frontend image.

## Known limitations

- Full dev stack not yet exercised end-to-end in containers or a browser.
- The CD pipeline and `deploy.sh` have not run on the Ubuntu VM yet; there is no automatic deploy or approval gate (pull-based by design for now).
- A rollback does not revert the database schema (restore the `pg_dump` manually if needed).
- The `error_handler` alert is an audit-log entry; there is no Sentry/Prometheus hook yet.
- Rate limiter is in memory per instance.
- A crash between the FHIR booking and the DB commit can leave a booking without session state.
- Consent is not yet tied to a fresh Gesundheits-ID authentication.
- The HAPI mock does not verify the SMC-B assertion.
- Production needs a real TI gateway/Konnektor, SMC-B/HSM-B and gematik-approved IdP.
