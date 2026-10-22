# Phase 4 — Authentication, identity and tenant boundaries

**Status:** Complete (static boundary review; adversarial HTTP confirmation is scheduled for Phase 12). **No source changes.**

## Authentication path

- [FACT] `authenticate_agent` maps nine fixed principal names to environment variables (`FRIDAY_API_KEY`, `INFERENCE_API_KEY`, `STRATEX_API_KEY`, `INTELX_API_KEY`, `FUTURIS_API_KEY`, `CORTEX_API_KEY`, `FORGE_API_KEY`, `SENTINEL_API_KEY`, `MEMORA_API_KEY`), accepts `X-API-Key` or Bearer, and compares with `hmac.compare_digest` (`apps/api/dependencies.py:50-96`).
- [FACT] Authentication returns the principal name only; it does not resolve a tenant. `get_actor_header` delegates directly to `authenticate_agent` (`apps/api/dependencies.py:10-16`). The model supports duplicate agent names in different tenants (`storage/relational/models.py:57-68`), while `IdentityService.get_agent_by_name` is globally unscoped whenever its optional `tenant_id` is omitted (`core/identity/service.py:169-186`).
- [FACT] Normal mode fails closed for missing credentials, unconfigured principal keys and invalid credentials. The explicit `MEMORA_ALLOW_ANONYMOUS_DEV` opt-in can return the caller-supplied agent name (or `friday`) with no credentials when the service is not in production (`apps/api/dependencies.py:72-103`). This is a development/test bypass and must remain off in deployed environments.

## Principal selection and cross-tenant enforcement

- [FACT] The context request body accepts `agent_id`; the route uses `req.agent_id or actor_name` without comparing it to the authenticated principal (`apps/api/routers/v1_context.py:16-25,41-60`). `ContextBuilderService` resolves any supplied ID/name and auto-registers a missing name (`core/memory/context/builder.py:91-95`) before searching as that actor (`:100-116`).
- [FACT] The v1 memory write body also selects the caller: `req.agent_id or actor_name` is passed to the pipeline as both caller and agent (`apps/api/routers/v1_memories.py:202-234`). `record-interaction` and `learn-outcome` similarly choose `req.agent_name or actor_name` (`apps/api/routers/v1_memories.py:669-676,807-824`).
- [FACT] The policy engine does compare the resolved actor tenant with the namespace tenant and denies mismatches (`core/policy/engine.py:64-77`), and checks explicit grant action/expiry on private/shared namespaces (`:104-163,178-222`). This protection is ineffective when a route lets an authenticated caller choose a different actor before the policy check.
- [INFERENCE] The core auth boundary is a named key, not a key-to-tenant principal object. Every route that accepts a body-selected identity or performs an unscoped name lookup therefore needs a route-level identity/tenant binding before using the model's RBAC rules.

## Namespace and identity administration

- [FACT] `/agents` is authenticated; creating an agent is restricted to the `memora` service identity (`apps/api/routers/agents.py:19-38`), but list/get operations do not pass an authenticated tenant to the lookup (`:78-87`; `IdentityService.list_agents` returns every row at `core/identity/service.py:192-194`).
- [FACT] Namespace creation allows client-supplied owner IDs/types and does not validate that the path root matches the authenticated agent (`apps/api/routers/namespaces.py:51-78`). `IdentityService.create_namespace` returns an existing same-tenant path unchanged, without reconciling owner/type (`core/identity/service.py:197-215`); subsequent normal agent registration asks to create its default private namespace (`:115-124`). This creates a namespace-preclaim/ownership-invariant risk, especially combined with client-selected identities and public types.
- [FACT] Authenticated namespace listing accepts an arbitrary `agent_id` query and delegates to a tenant-default listing query rather than binding the filter to the authenticated actor (`apps/api/routers/namespaces.py:80-82`, `core/identity/service.py:272-277`).
- [FACT] The `/v1/namespaces/{namespace_id}/policy` handler looks up by ID/path without a tenant predicate and returns owner and grant metadata without an authorization check (`apps/api/routers/v1_namespaces.py:14-40,50-59`).
- [FACT] A separate owner check exists for `/namespaces/grants` and `/namespaces/grants` revocation (`apps/api/routers/namespaces.py:24-48,84-130`); it does not protect the distinct `/v1/memories/{memory_id}/share` path, covered in Phases 5 and 12.
- [FACT] Tenant IDs and per-tenant unique indexes are present on agents/namespaces, and memories have tenant/index fields (`storage/relational/models.py:57-68,88-110,158-228`). They are not a substitute for deriving tenant identity from the credential or applying tenant filters on every read/mutation.

## Runtime status and exit check

- [FACT] Phase 0 contains isolated, credentialed probes of body-selected victim identity and namespace preclaim. Phase 12 will rerun the critical cases with isolated fixtures and report exact outcomes.
- [FACT] No production credential was used, no cloud endpoint was contacted, and no personal/secret value is reproduced.
- [INFERENCE] Identity spoofing is a high-priority boundary flaw because downstream private-by-default checks trust the resolved model actor rather than the credential-authenticated name.
- [FACT] Phase 4 is complete; next is the domain model, access policy, and lifecycle state machine.
