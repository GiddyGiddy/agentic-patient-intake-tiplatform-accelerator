# Agentic Patient Intake 

Agentic AI patient intake prototype on the Telematikinfrastruktur (TI): anamnesis, experimental urgency labels, ePA consent, FHIR appointment booking in the KIS and a KIM letter to the referring doctor. Built with React, FastAPI, LangGraph, PostgreSQL and a local LLM. Runs **on-premise** with Podman; the TI services (ti-gateway, KIM-CM, ID services) are simulated by local mocks for development and end-to-end tests.


## Current status

| Area | Status |
|---|---|
| Frontend and backend | Prototype implemented: chat, reference BFF and mock OIDC login, simulated consent, agent workflow, booking and KIM handoff; automated tests are included. |
| Persistence | Intake sessions, consent, audit and KIM outbox use the configured database; reference BFF login sessions are separate and in memory. CI tests SQLite and PostgreSQL. |
| Local development | Compose provides the reference browser login and mock TI services. A browser run-through with synthetic data has exercised login, simulated consent, booking and mock KIM handoff. |
| Staging | The overlay uses mock OIDC login, not the local reference BFF. It configures 15-minute mock tokens and Ollama keep-alive; deployment of these settings requires updated images and has not been verified on the staging server. |
| CI | GitHub Actions runs tests, security scans, Compose validation and image builds. Check the latest run for its current status; dependency and secret scans can stop early if a preceding audit fails. |
| CD | Pull-based GHCR image deployment with `deploy/deploy.sh` and a staging overlay. A production deployment and real TI integration have not been verified. |
| Production readiness | Not clinically validated or production-ready. Real Gesundheits-ID, ePA authorization and TI integration require implementation, security assessment and operational approval beyond changing URLs or configuration. |

## Solution Architecture

![Agentic Patient Intake on-premise solution architecture](./docs/solution-architecture.svg)

The backend, database and LLM run in the clinic's own data center. Nothing is sent to a public cloud.

### Components

| Component | Technology | Where | Dev mock |
|---|---|---|---|
| Frontend | React 18, nginx | On-premise | – |
| Backend | FastAPI, LangGraph, SQLAlchemy | On-premise | – |
| Database | PostgreSQL 16 (only persistent store) | On-premise | – |
| LLM | Ollama or Foundry Local | On-premise | – |
| Gesundheits-ID (OIDC) | OpenID Connect / OAuth2 | TI | `mock-id-dienst` :8081 |
| TI gateway / Konnektor (HSM-B / SMC-B) | Reverse proxy, signs requests, adds Telematik-ID | TI | `mock-ti-gateway` :8082 |
| ePA + KIS (FHIR / ISiK) | HAPI FHIR | TI / clinic | `mock-kis-fhir` :8080 (+ `mock-kis-seed`) |
| KIM client module | SMTP | TI | `mock-kim-cm` :2525 |

## Start

```powershell
podman compose up --build
```

The local stack uses the **reference Federation browser login by default**.
Open `http://127.0.0.1:3000/`; `http://localhost:3000/` redirects there because
the test callback and cookies use `127.0.0.1`. Ports 3000, 8000, 8083 and 8085 must be
available. Stop any separately running reference browser or IdP/Fedmaster
containers using those ports first. This fixed local profile does not use
`FRONTEND_PORT` or `BACKEND_PORT`.

The reference IdP and Fedmaster, their public test key and HTTP endpoints are
strictly for synthetic data. BFF sessions are held in one backend process and
are lost on restart. In the default local mock profile, after successful ID-token
validation the BFF session has a fixed 15-minute lifetime, independently of the
shorter ID-token expiry; it does not refresh or extend the token. This local-only
behavior is enabled by `REFERENCE_DEMO_SESSION_TTL_S=900` and requires the mock
profile. With the setting omitted, reference sessions remain capped by the token
expiry. Real ePA authorization is not implemented. The default local
stack enables `REFERENCE_MOCK_EPA_ENABLED` for explicit consent to read synthetic
data from the local FHIR mock and continue with mock booking and KIM. This is
not an ePA permission or an authorization granted by the Gesundheits-ID. The
backend fails closed if the reference flags, loopback frontend origin, mock
gateway URLs or mock KIM endpoint do not match the local profile; the switch
defaults to false outside this Compose stack. These endpoint checks do not prove
that a separately configured service is trustworthy. The login is not a
production trust implementation and must not be used with real patient
information. Staging continues to use its existing
mock login; the loopback-only reference profile is not suitable for its remote
HTTPS deployment.

| Service | URL |
|---|---|
| Frontend | http://127.0.0.1:3000 |
| Backend (OpenAPI) | http://127.0.0.1:8000/docs |
| Reference IdP | http://127.0.0.1:8085 |
| Reference callback | http://127.0.0.1:8083/auth |
| Mock ID-Dienst | http://localhost:8081/.well-known/openid-configuration |
| Mock TI gateway | http://localhost:8082/health |
| Mock KIS/ePA (HAPI FHIR) | http://localhost:8080/fhir |
| Mock KIM-CM (SMTP) | localhost:2525 |
| LLM (Ollama) | http://localhost:11434 (`podman compose exec llm ollama pull llama3.2:3b`) |

The local Ollama service uses `OLLAMA_KEEP_ALIVE=15m`: after an inference the
model remains loaded for 15 minutes by default, subject to resource pressure.
This consumes memory and does not preload the model after an Ollama restart.
With staging mocks enabled, the staging overlay also uses a 15-minute Ollama
keep-alive and 15-minute mock IdP access tokens. Staging still uses the mock OIDC
browser login, not the local reference BFF; these settings do not affect production.
See [staging deployment notes](docs/cd-setup.md) for the required image update.

Demo patients: `X110000001` (medications, an allergy, and a condition in the mock ePA), `X110000002` (no ePA entries), and `X110411675` (the reference IdP's default test KVNR, with synthetic medication, allergy, and condition data). After the explicit local demo consent, the default reference flow can read these mock records and perform mock booking; neither the login nor this consent grants access to a real ePA. In the separate mock-login flow, the `kvnr` claim is used as a FHIR identifier, not as the internal session key. Scheduling requires exactly one matching KIS patient; missing, invalid, or duplicate matches prevent booking and prompt the patient to contact the practice. If the LLM is unavailable, the prototype does not complete a non-emergency assessment or assign a fallback urgency. Existing red-keyword alerts do not wait for the LLM. On Windows, use `127.0.0.1` instead of `localhost` for published database ports.

### Optional gematik sectoral IdP reference server

The separate [`compose.gsi.yml`](./compose.gsi.yml) builds gematik's public
[`app-gemSekIdp`](https://github.com/gematik/app-gemSekIdp) at a pinned commit.
It is a **local interoperability experiment**. The default local stack uses
the reference browser profile; staging continues to use `mock-id-dienst`.
Use synthetic data only. The reference server is bound to the local host and
its request and gematik INFO logging are disabled.

```powershell
podman compose -f compose.gsi.yml up -d --build
$env:GSI_TEST_BASE_URL = "http://127.0.0.1:8085"
$env:GSI_TEST_MANAGEMENT_URL = "http://127.0.0.1:8185"
python mock-ti-services\gematik-reference\test_reference_server.py
podman compose -f compose.gsi.yml down
```

In a network that intercepts TLS, Maven may need a trusted corporate root CA
in its build-stage Java trust store. With Podman, build using a read-only
certificate mount and pass its container path as `MAVEN_CA_CERT`; the mount
source must be accessible to the Podman machine. For example, after verifying
the certificate's provenance and location:

```powershell
$caPath = "/etc/pki/ca-trust/source/anchors/your-trusted-root.crt"
podman build --volume "${caPath}:/tmp/extra-ca.crt:ro" --build-arg MAVEN_CA_CERT=/tmp/extra-ca.crt -t agentic-patient-intake-gematik-accelerator-gsi-server -f mock-ti-services\gematik-reference\Dockerfile mock-ti-services\gematik-reference
podman compose -f compose.gsi.yml up -d --no-build
```

The optional CA is used only in the build stage; TLS verification stays enabled.
The initial smoke tests inspect metadata but do not validate its signature or a federation
trust chain. `GSI_FEDMASTER_MODE=local` skips Fedmaster verification, and the
reference server returns a placeholder access token. The default local BFF
separately validates reference ID tokens against a pinned test chain; production
RP registration, production trust validation and separate ePA authorization
are not integrated.
This setup is not suitable for real patient data or production use.

For an additional **isolated protocol test**, build the test RP image and run it
against the already-running reference server:

```powershell
podman compose -f compose.gsi.yml build gsi-rp-test
podman compose -f compose.gsi.yml run --rm --no-deps gsi-rp-test
```

If pip also requires the trusted CA, build the test image separately, using the
verified Podman-machine path from above, and then run the same test command:

```powershell
podman build --volume "${caPath}:/tmp/extra-ca.crt:ro" --build-arg PIP_CERT=/tmp/extra-ca.crt -t patient-intake-gsi-rp-test -f mock-ti-services\gematik-reference\Dockerfile.test mock-ti-services\gematik-reference
podman compose -f compose.gsi.yml run --rm --no-deps gsi-rp-test
```

The test RP generates ephemeral keys and serves a signed entity statement only
on the server container's loopback interface. It exercises PAR, PKCE, simulated
authentication, an encrypted ID token, and selected failure cases. It verifies
the server entity statement and signed JWKS using a public key pinned to the
reference source, then verifies issuer, audience, expiry, nonce and signature of
the decrypted token. This is a **test-only pinned trust anchor**, not a
Federation trust chain. The local Fedmaster mode does not validate the RP
statement's signature. The flow does not log the patient into this application,
obtain an ePA access token, or test a browser; only synthetic identities are
allowed. Run tests sequentially because the test RP uses loopback port 8084.

The optional Fedmaster test exercises the published reference Fedmaster and its
signed statement about the configured reference IdP:

```powershell
podman compose -f compose.gsi.yml --profile federation build gsi-fedmaster
podman compose -f compose.gsi.yml --profile federation up -d --no-build gsi-fedmaster
podman compose -f compose.gsi.yml run --rm --no-deps gsi-rp-test python test_federation_chain.py -v
```

In the CA-intercepted environment described above, use this build command instead:

```powershell
podman build --volume "${caPath}:/tmp/extra-ca.crt:ro" --build-arg MAVEN_CA_CERT=/tmp/extra-ca.crt --build-arg REFERENCE_MODULE=gsi-fedmaster -t patient-intake-gsi-fedmaster -f mock-ti-services\gematik-reference\Dockerfile mock-ti-services\gematik-reference
```

This checks the test Fedmaster's self-statement, its signed statement about the
IdP, the IdP's self-statement and rejection of an unknown IdP. Its pinned public
key is a **test-only** trust anchor. The separate IdP used by this test runs in
local Fedmaster mode; this test does not validate the RP's federation membership or
perform a complete production Federation policy resolution. The application's
local reference login verifies a limited test chain separately. The test rejects statements containing
`metadata_policy`, `metadata_policy_crit`, or `constraints` rather than silently
ignoring them; it does not evaluate these policies or constraints.

#### Remote Fedmaster RP integration test

The separate `federation-rp` profile starts a second IdP and Fedmaster in one
isolated container network namespace. The IdP uses `GSI_FEDMASTER_MODE=remote`.
The Fedmaster is configured to register the test RP's signing public key; the
test rejects an unknown RP and an incorrectly signed RP entity statement before
running PAR/PKCE and validating the encrypted ID token.

Build the images as described above, then run:

```powershell
podman compose -f compose.gsi.yml --profile federation-rp up -d --no-build --wait gsi-server-federated
podman compose -f compose.gsi.yml run --rm --no-deps gsi-rp-federated-test
```

For each subsequent run, restart the isolated IdP first and wait for readiness:

```powershell
podman compose -f compose.gsi.yml stop gsi-server-federated
podman compose -f compose.gsi.yml --profile federation-rp up -d --no-build --wait gsi-server-federated
podman compose -f compose.gsi.yml run --rm --no-deps gsi-rp-federated-test
```

The restart is necessary because the reference server caches accepted RP
statements; otherwise the invalid-signature case would not exercise verification.
The test image contains an upstream **publicly distributed test-only private key**;
its public key is registered in the isolated test Fedmaster. This key must never be used for real
identities, deployed services, or production trust. The RP encryption key remains
ephemeral. These tests demonstrate this reference implementation's signature
checks; they do not prove complete OpenID Federation policy validation, mTLS,
browser login, or real ePA authorization. The test checks the reference
Fedmaster's fixed test redirect URI but does not execute a browser callback.
The default local login uses a separate BFF integration of the reference chain.

To stop only the isolated remote test services, in dependency order:

```powershell
podman compose -f compose.gsi.yml stop gsi-server-federated
podman compose -f compose.gsi.yml stop gsi-fedmaster-rp
```

The backend also exposes a reference-only RP entity statement at
`/.well-known/openid-federation`. It is enabled in the default local stack but
disabled by default in the backend configuration. For isolated tests
only, set `REFERENCE_RP_ENABLED=true`, `REFERENCE_RP_SIGNING_KEY_FILE` and
`REFERENCE_RP_ENCRYPTION_KEY_FILE` to readable PEM P-256 private-key files. The
keys must be distinct. The signing public key must match the test RP entry in
the Fedmaster; the remote test profile above registers the upstream published
test signing key. Never use that public test private key outside this experiment.
The endpoint advertises the reference profile's fixed test entity ID and
redirect URI; the browser callback additionally requires the BFF and callback
proxy. A missing, unsupported,
or reused key causes a 503 rather than silently generating a replacement.
This endpoint alone does not establish trust or implement application login.
The backend's `reference_trust` module can verify the isolated test Fedmaster,
IdP statement, fixed endpoints and signed JWKS. It rejects unsupported
policies/constraints rather than evaluating them. It is not yet connected to a
general-purpose production trust resolver.

#### Reference browser login (local test profile)

The `browser` profile runs a separate, single-process backend and frontend with
the reference Fedmaster and remote-mode IdP. The backend verifies the pinned
test chain, starts PAR with server-side PKCE, validates the encrypted signed
ID token and creates a short-lived HttpOnly cookie session. The callback proxy
routes the Fedmaster's fixed test redirect at `127.0.0.1:8083/auth` to the
backend. Neither ID tokens nor the reference placeholder access token are
exposed to the frontend.

This profile uses HTTP, publicly known reference signing keys, synthetic
identities and an in-memory BFF session store. It is **not** a production or
staging deployment and must not be used with real patient data. Restarting the
backend invalidates all BFF sessions; use only one backend worker. Federation
policy resolution remains incomplete and the reference login does not grant
real ePA authorization. The isolated browser profile does not enable the local
mock ePA switch and consent returns 503. The default local stack enables the
separate simulated consent only for its configured mock services. Neither mode
sends the reference ID token or placeholder access token to the gateway.

After building the reference IdP, Fedmaster and test RP images as above,
ensure local ports 3000, 8000, 8083 and 8085 are available, then run:

```powershell
podman compose -f compose.gsi.yml --profile browser build gsi-browser-backend gsi-browser-frontend
podman compose -f compose.gsi.yml --profile browser up -d --no-build gsi-browser-frontend
```

On CA-intercepted networks, build those images with the verified read-only CA
mount as described above, tagging them `patient-intake-reference-backend` and
`patient-intake-reference-frontend`. For the frontend build pass
`REACT_APP_API_URL=http://127.0.0.1:8000` and `REACT_APP_REFERENCE_LOGIN=true`.

Open `http://127.0.0.1:3000/`; `http://localhost:3000/` redirects there because
cookies are host-bound.
The isolated profile is separate from the default local and staging stacks;
it does not change the production configuration. The
profile's encryption test key is stored in its named volume and the public
upstream signing fixture is copied into it. Neither is appropriate for real
trust. To stop only the browser profile services:

```powershell
podman compose -f compose.gsi.yml stop gsi-browser-frontend gsi-browser-callback
podman compose -f compose.gsi.yml stop gsi-browser-backend gsi-browser-server
podman compose -f compose.gsi.yml stop gsi-browser-fedmaster
```

All environments default to `llama3.2:3b` (the development `.env`, the Compose fallback, the backend default and
`deploy/prod.env.example`). To evaluate another locally installed
Ollama model, set `LLM_MODEL` in the shell before starting or recreating the backend; in
PowerShell, for example, use `$env:LLM_MODEL = "<model-name>"` and then
`podman compose up -d --no-build backend`. Changing the shell variable does not update an
already running container. Model output and triage must be evaluated using synthetic data;
neither the model nor the keyword rules are validated for clinical use.

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
it for a benchmark. An unavailable or invalid intake response stops automatic
non-emergency assessment; the KIM letter can fall back to a template.

## Agent flow

```
POST /api/chat → intake_agent (anamnesis, experimental urgency labels)
      → consent_agent (stops until consent is granted)
      → scheduling_agent (FHIR Slot + Appointment in the KIS)
      → kim_agent (Arztbrief + triage.json → KIM outbox) → END
      scheduling failure (gateway/SMC-B error, timeout) → error_handler → END
```

- Consent, triage and conversation are held **server-side** per patient; the client cannot set them.
- The appointment result displays the agent's anamnesis summary returned by the server, or indicates when it is unavailable.
- Intake follow-up questions are retained with the patient's answers in the short-lived server-side session so the summary can interpret answers in context. The original question-and-answer record accompanies the generated summary for review; if the LLM cannot provide a summary, the result explicitly says so. Generated text can omit or misstate details, is not a verified clinical assessment, and needs clinician review.
- An invalid or unavailable LLM follow-up or urgency response, or a still-incomplete interview at the question limit, stops automatic completion and asks the user to contact the practice. Strict response validation checks structure, not medical correctness. Existing red keywords return an alert without an LLM call. This is not a comprehensive or clinically validated emergency detector.
- Repeated follow-up questions are checked against the recorded conversation using normalized text and a conservative similarity heuristic. The model gets one chance to ask a different question; if it fails, the interview stops without an urgency score and must be restarted. Red-keyword messages submitted through the API can still trigger an emergency alert. This cannot reliably detect all paraphrases and may occasionally flag different questions as similar.
- KIM `triage.json` now uses `triage_system: "unvalidated-prototype"` rather than claiming Manchester; downstream consumers must not interpret it as a validated triage assessment.
- In the mock-login flow, after consent, medication, allergy, and condition resources are read independently with the
  mock patient's token. In the default reference BFF's simulated mode they are read from the local mock without a bearer token; no real ePA authorization exists. Partial reads and HTTP 401/403 are reported without blocking
  scheduling; 403 is not interpreted as an ePA opt-out. ePA allergies and conditions remain
  separate from patient-reported symptoms and triage and are labeled as ePA data in the KIM letter.
- Red/orange patients get acute slots, green patients standard slots.
- KIS/gateway calls use a connect/read timeout (`FHIR_CONNECT_TIMEOUT_S`, `FHIR_READ_TIMEOUT_S`). Failures are mapped to `GATEWAY_TIMEOUT`, `GATEWAY_CONNECTION_FAILED`, `GATEWAY_AUTH_ERROR` or `GATEWAY_ERROR`; the `error_handler` node writes an alerting audit event (`scheduling_failed`), shows the patient a neutral German fallback message (no stack trace) and ends the run. The API response then has `scheduling_failed: true`.
- The graph is invoked in `backend/app/api/endpoints.py` (`_run`).
- For the request-by-request sequence, shared-state fields, and mock-service boundaries, see [docs/agent-sequence.md](docs/agent-sequence.md).

## Backend API

The `/api` endpoints require a reference BFF session cookie in the default
local stack; the separate mock-login flow uses a bearer token. `/health` is
unauthenticated.

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` `{message}` | Adds a patient message and runs the graph. Asks follow-up questions until triage is set, then waits for consent. |
| `POST /api/consent` `{granted}` | Grant resumes the mock workflow: medication plan, FHIR Appointment, KIM letter. Idempotent after completion. `granted:false` revokes. Reference-mode grant returns 503 unless the explicit local mock setting and endpoint checks pass; it never grants real ePA access. |
| `DELETE /api/session` | Start over / logout: drops conversation, triage and consent. |
| `GET /health` | Status including database reachability (503 if the DB is down). |

Errors: `502` TI service (KIS/ePA) unreachable (retry is safe), `409` intake already completed or none active, `422` message too long, `429` rate limit. A failed KIM hand-over does not fail the run; the response has `kim_sent: false` and the outbox retries.

## Podman build behind a trusted TLS-inspecting proxy

If pip reports `CERTIFICATE_VERIFY_FAILED` during the backend build, ask your
IT team to configure and verify the organization's CA in the Podman VM. On a
Podman WSL machine that already has the CA in its system bundle, build the
backend without disabling TLS verification:

```powershell
podman build --volume /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem:/run/build-ca.pem:ro --build-arg PIP_CERT=/run/build-ca.pem -t agentic-patient-intake-gematik-accelerator-backend .\backend
podman compose up -d --no-build --no-deps backend
```

The bundle path is inside the Podman VM and may differ on other setups. The CA
bundle is mounted only during the build; the final image does not retain
`PIP_CERT`. The normal `compose --build` command does not pass this build-only
mount.

For the local frontend, use the same bundle for npm and Alpine, then recreate
only the frontend:

```powershell
podman build --volume /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem:/run/build-ca.pem:ro --build-arg NODE_EXTRA_CA_CERTS=/run/build-ca.pem --build-arg SSL_CERT_FILE=/run/build-ca.pem --build-arg REACT_APP_API_URL=http://127.0.0.1:8000 --build-arg REACT_APP_REFERENCE_LOGIN=true -t agentic-patient-intake-gematik-accelerator-frontend .\frontend
podman compose up -d --no-build --no-deps frontend
```

The default reference stack uses fixed loopback ports and does not honor
`BACKEND_PORT`. These are local development URLs; production frontend URLs
must match its deployment.

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

- The local browser run-through used synthetic data and mock services; it is not validation of a real TI or clinical workflow.
- The staging reference BFF login is not implemented. The staging overlay's mock login and lifetime settings require deployed matching images; the remote deployment has not been verified.
- Production deployment and real TI integration have not been verified; deployment is pull-based without an automatic deploy or approval gate.
- A rollback does not revert the database schema (restore the `pg_dump` manually if needed).
- The `error_handler` alert is an audit-log entry; there is no Sentry/Prometheus hook yet.
- Rate limiter is in memory per instance.
- A crash between the FHIR booking and the DB commit can leave a booking without session state.
- Consent is not yet tied to a fresh Gesundheits-ID authentication.
- The HAPI mock does not verify the SMC-B assertion.
- Production needs a real TI gateway/Konnektor, SMC-B/HSM-B and gematik-approved IdP.
