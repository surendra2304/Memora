# Phase 5 — Domain model, policy and lifecycle

**Status:** Complete. **Evidence:** ORM/Pydantic schemas and policy/lifecycle services were read; runtime behavior is reserved for Phase 12. No source changes.

## Canonical model

- [FACT] The main domain has 5 namespace types (`AGENT_PRIVATE`, `PROJECT_PRIVATE`, `TEAM_SHARED`, `UNIVERSE_GLOBAL`, `PUBLIC`), 11 memory types, and 6 lifecycle states (`CANDIDATE`, `ACTIVE`, `VERIFIED`, `SUPERSEDED`, `ARCHIVED`, `DELETED`) (`storage/relational/models.py:24-50`).
- [FACT] Nine SQLAlchemy entities define the persistence model: Agent, Namespace, AccessGrant, MemoryRecord, MemoryRelationship, AuditLog, DeletionTombstone, EventLog and EventConsumerCursor (`storage/relational/models.py:52-338`).
- [FACT] Agent and namespace names/paths are unique per tenant (`storage/relational/models.py:57-68,88-110`). Grants contain tenant, agent, namespace, action list, purpose and optional expiration (`:120-153`). Memory records include tenant/user/agent/workspace/device/task identity fields, content, provenance, entities, confidence/importance, lifecycle, temporal bounds and a per-(tenant,agent,key) idempotency index (`:158-228`).
- [FACT] Graph edges reference memory IDs but have no independent tenant column; tenant enforcement is therefore by related records and service-layer checks (`storage/relational/models.py:233-255`). Audit and event tables carry tenant fields; event cursors are keyed by tenant, agent and consumer (`:257-338`).
- [FACT] Pydantic write/query schemas expose caller-supplied tenant/owner/namespace fields, lifecycle state, provenance/trust values and user/workspace/task scopes (`core/memory/schemas.py:34-72,74-118,148-180`). Whether individual routers trust or override those fields is examined in Phases 6 and 12.

## Policy behavior and mismatches

- [FACT] `PolicyEngine.evaluate_access` records five audit dimensions (who/what/where/why/how_long) and denies an actor/namespace tenant mismatch (`core/policy/engine.py:46-77`). For another agent's private namespace it checks grant expiry and action (`:104-163`); project/team-style namespaces similarly require ownership or a matching grant (`:178-236`). A bounded agent is constrained by namespace type and path prefix (`:80-101`).
- [FACT] The policy code records `purpose` in audit dimensions and uses it in a reason string, but does not compare the request purpose with `AccessGrant.purpose` in either grant branch (`core/policy/engine.py:56-61,118-154,191-227`). [INFERENCE] Purpose-labeled grants are not purpose-bound enforcement; purpose currently functions as metadata for these branches.
- [FACT] The `PUBLIC`/`UNIVERSE_GLOBAL` branch returns `allowed=True` for any requested action without checking the action (`core/policy/engine.py:165-176`), while the namespace descriptions call both “Open Read” / “Public Read” (`apps/api/routers/v1_namespaces.py:42-47`). This mismatch has a destructive runtime confirmation in Phase 0 and Phase 12.
- [FACT] The policy engine does not enforce trust level or lifecycle state as policy dimensions. Trust/lifecycle filtering exists in query/search code, but is separate from the policy decision. This is narrower than README language describing a policy engine over trust and lifecycle (`README.md:29-32`).
- [FACT] `PolicyEngine` writes a metric and emits denied-access events, then adds an `AuditLog` row when requested (`core/policy/engine.py:238-287`). It flushes and suppresses flush errors rather than proving audit durability in every caller (`:282-287`).

## Lifecycle and forgetting

- [FACT] Lifecycle transitions are explicitly enumerated; `DELETED` has no outgoing transition, while same-state requests are considered valid (`core/lifecycle/state_machine.py:13-48`). Transitioning to `VERIFIED` always updates `last_verified_at` and adds 0.1 confidence, capped at 1.0 (`:51-71`).
- [INFERENCE] Because same-state `VERIFIED` is allowed and the verification transition adds confidence unconditionally, repeated verification can increase confidence without new evidence. `MemoryService.verify_memory` accepts notes but no evidence reference and invokes that transition (`core/memory/service.py:366-396`).
- [FACT] Supersession ranks candidates by confidence, owner/source authority and provenance richness rather than recency alone (`core/lifecycle/supersession.py:26-59,61-116`).
- [FACT] Decay is cursor/batch based, processes candidate and active memories, derives importance from a stored baseline plus age, honors pinned/high-importance records, archives expired/cold records, and commits each batch (`core/lifecycle/decay.py:12-117`). Tenant filtering is optional (`:14-22,33-42`); the API/service's omission of a tenant filter is treated in Phase 12.
- [FACT] Hard deletion has a tombstone model tracking relational/vector/cache/graph convergence (`storage/relational/models.py:282-305`). The actual deletion/retry operations are analyzed in Phase 8.

## Exit check

- [FACT] This phase is code/model review; no database migration or external backend was exercised here.
- [INFERENCE] Tenant isolation, action authorization and lifecycle validity are application-level invariants across tables, not fully captured by composite foreign keys. Every route/service path therefore needs the same actor and tenant checks.
- [FACT] Phase 5 is complete; next is the write pipeline, safety gates and idempotency.
