# Phase 12 — Controlled security and privacy verification

**Status:** Complete. Probes used synthetic data and credentials against the FastAPI app with an isolated in-memory SQLite session. No application source or tests were changed. No external service was contacted; optional Turso/Redis services were disabled, and the Qdrant target was an unavailable loopback address. Results below describe the tested code paths, not every deployment or runtime configuration.

> **SECURITY NOTICE — committed local database credentials:** `docker-compose.yml:10,24-25` contains literal database connection/user/password values in tracked configuration. They appear to be local Compose/demo defaults; this review did not establish that they authenticate to a live or shared database. Treat them as exposed, do not reuse them outside isolated local development, and rotate them if they have ever been reused. Their values are intentionally omitted here and in the final report.

## Method and scope

- [FACT] The probes used `fastapi.testclient.TestClient`, `Base.metadata.create_all()` on an in-memory SQLite engine with `StaticPool`, seeded Friday/Forge agents, a single overridden `get_db` dependency, and synthetic marker records. The local app startup reported Turso sync disabled. The tested credential was supplied through test-process environment variables; no real repository credential was used or printed.
- [FACT] `authenticate_agent` maps `X-Agent-Name` to a named environment credential and rejects missing or mismatched credentials when anonymous development access is disabled (`apps/api/dependencies.py:50-103`). The isolated probe returned HTTP 401 with no credentials and with an invalid credential, and HTTP 200 for an authenticated Friday request.
- [FACT] An initial context-result check searched the entire JSON response for the marker. That check was discarded because the request query is echoed in the response. The final check parsed the `memories` array and compared only returned `content_text` fields; no finding below relies on the discarded substring check.
- [FACT] Every seeded record was synthetic and the database was discarded after each probe. No source files, tracked databases, remote APIs, or production credentials were involved. The local TestClient emitted a Starlette/httpx deprecation warning; it did not change the observed status or persisted data.

## Confirmed observations

### Authenticated principal can be replaced by a request-body identity

- [FACT] `POST /v1/context` obtains the authenticated header identity, then uses `req.agent_id or actor_name` as the identity passed to `ContextBuilderService` (`apps/api/routers/v1_context.py:41-63`). With a Friday credential and `agent_id` omitted, the parsed response contained zero memories from the synthetic Forge-private namespace; the same request with body `agent_id="forge"` returned one matching Forge-private marker (HTTP 200 in both cases).
- [FACT] `POST /v1/memories` similarly computes `calling_agent = req.agent_id or actor_name` and passes it as both caller and agent identity (`apps/api/routers/v1_memories.py:117-131,202-234`). With a Friday credential, writing to `memora://forge/private` and omitting body `agent_id` returned 403; adding body `agent_id="forge"` returned 201, with Forge as the response agent and record owner.
- [INFERENCE — high-priority authorization defect] In the tested routes, the API credential authenticates Friday but a caller-controlled body field selects Forge for authorization and memory operations. These results demonstrate cross-agent private-memory disclosure through context assembly and cross-agent write impersonation in the tested SQLite/API configuration. The credential-to-body binding should be treated as untrusted until enforced; no source fix was made in this review.

### Tenant-scoped reads and mutations are not consistently enforced

- [FACT] A Friday-authenticated `GET /v1/reflection/insights` returned a synthetic reflection insight seeded under a different tenant (HTTP 200). The route filters by `memory_type` and reflection provenance but does not add a tenant predicate or call the namespace policy (`apps/api/routers/v1_reflection.py:57-95`).
- [FACT] A Friday-authenticated `POST /v1/memories/decay` changed a synthetic record seeded in another tenant from importance `0.90` to `0.56` (HTTP 200). The route passes the actor name to `MemoryService.apply_decay` (`apps/api/routers/v1_memories.py:641-657`), but that service invokes the decay engine without its optional `tenant_id` argument (`core/memory/service.py:634-659`). The engine only adds a tenant filter when that argument is non-null (`core/lifecycle/decay.py:14-42`).
- [INFERENCE — high-priority privacy/integrity defects] In a multi-tenant deployment, the observed unscoped reflection query can disclose another tenant's reflection data, while any valid caller able to invoke decay can mutate records outside its tenant. The probes confirm the endpoint behavior against synthetic tenants; they do not establish the population or effect on any live deployment.

### Direct legacy ingestion can self-assert verified status

- [FACT] The authenticated legacy `POST /memories` request accepted a synthetic semantic record with caller-supplied `lifecycle_state="verified"` and caller-supplied provenance `trust_level="verified"`, returning HTTP 201 with both values preserved. The route calls `MemoryService.create_memory` (`apps/api/routers/memories.py:53-66`); the input schema exposes lifecycle/provenance fields (`core/memory/schemas.py:85-107`), and the service assigns them directly after a write-policy check without the promotion evidence gate (`core/memory/service.py:106-109,135-170`).
- [FACT] The explicit promotion workflow separately requires verification evidence and a confidence threshold before setting a record to verified/semantic (`core/memory/service.py:546-579`).
- [INFERENCE — high integrity risk] A normal authenticated legacy-route caller can cause an initial memory to carry a verified lifecycle label and trust claim without passing the explicit promotion workflow. Downstream consumers must not treat legacy-supplied lifecycle/provenance values as independently verified until the write path enforces the state transition contract.

### Public/global namespace writes are accepted by the policy path

- [FACT] A Friday-authenticated `POST /v1/memories` to the seeded `memora://universe/global` namespace returned HTTP 201 and persisted the synthetic record in that namespace. `PolicyEngine.evaluate_access` returns an allowed decision for `UNIVERSE_GLOBAL` and `PUBLIC` namespaces without branching on the requested action; its stated rationale describes these namespaces as openly readable (`core/policy/engine.py:165-175`).
- [INFERENCE — design/authorization risk] The code and runtime permit an ordinary authenticated worker to write to a namespace whose policy comment describes it as openly readable. If global/public namespaces are intended to be read-only for ordinary agents, this is a cross-agent integrity/poisoning path; if broad writes are intentional, the policy and threat model should state that explicitly. No intended-write-policy evidence was found in this probe.

### Semantic-tier guard has an omission path; secret-content gate works on v1

- [FACT] A v1 write with `memory_type="semantic"`, source `agent:friday`, and omitted `source_type`/`trust_level` returned HTTP 201; the resulting provenance was canonicalized to `trust_level="candidate"`. The otherwise equivalent request with explicit candidate trust returned 403. Request fields default those metadata values to `None` (`apps/api/routers/v1_memories.py:117-135`); the semantic guard checks the pre-canonical values (`core/memory/pipeline/write_service.py:205-223`) and applies its candidate default later (`:326-359`).
- [INFERENCE — classification/policy gap] Omitted trust metadata lets a candidate-trust item enter the semantic memory type even though the guard rejects an explicitly candidate-trust item. The observed record remained labelled `candidate`, not `verified`; this is a semantic-tier classification inconsistency, not evidence that it was trusted or promoted.
- [FACT] Posting synthetic secret-shaped content to `POST /v1/memories` returned HTTP 422 and did not persist a memory. The route maps scanner violations to 422 (`apps/api/routers/v1_memories.py:290-303`); scanner coverage and the legacy-route discrepancy are documented in Phase 6 (`notes/phase-06-ingestion-safety-idempotency.md`). The same legacy endpoint has previously accepted the synthetic scanner fixture, so the v1 and legacy ingestion paths do not offer equivalent secret scanning.

## Tracked-file credential-pattern scan

- [FACT] A line-by-line run of the repository's `SecretScanner.scan_content` over all 211 tracked UTF-8 files examined 211 files, skipped none, and found 49 unique line locations in 21 files. Category counts were: AWS Access Key 2; Bearer Token 1; GitHub Personal Access Token 2; Google API Key 1; Hardcoded Password 2; OpenAI API Key 8; Unquoted Credential Assignment 33. These are regex matches, not a count of live credentials; the findings include known synthetic examples, scanner demonstrations, test fixtures, and code/config assignments.
- [FACT] Running the same scanner directly against tracked `docker-compose.yml:10,24-25` returned no categories for those lines, despite the literal credentials noted above. The scanner's implemented patterns and key-name allowlist are finite (`core/memory/pipeline/secret_scanner.py:16-56`).
- [INFERENCE — scanner coverage gap] The repository's content scanner is not a repository secret-management control and did not identify the Compose credential fields in this review. A dedicated tracked-file secret scanner and external credential rotation/revocation process are still needed; no external scanning service was used.
- [FACT] A long secret-shaped value in `scripts/run_e2e_system_test.py:103` was inspected only with the value redacted and appears to be a synthetic test fixture. `.gitguardian.yaml` contains documented synthetic examples. Neither is treated as a live credential in this report. A historical report contains personal information at `MEMORA_PHASE2_BUG_REPORT.md:182,372`; none is copied here.

## Exit check and handoff

- [FACT] Phase 12 separates static code evidence from runtime observations, includes corrected response parsing, identifies the exact synthetic/local setup, and preserves values and personal data by omission. It found multiple confirmed authorization/integrity concerns alongside working fail-closed authentication and the v1 content scanner.
- [FACT] No application source or test was changed. Phase 12 is complete; Phase 13 will inspect deployment/build/configuration artifacts and operational safety.
