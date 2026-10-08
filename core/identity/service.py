"""
Identity and Namespace Resolution Service
Manages registered ecosystem agents, parent-subagent delegation with bounded contexts,
and dynamic URI namespace resolution.
"""
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from storage.relational.models import Agent, Namespace, NamespaceType, AccessGrant

#: Path roots that are not owned by a single agent. Everything else in the first
#: segment of a namespace path is the name of the agent that owns the space.
OPEN_NAMESPACE_ROOTS = frozenset({"universe", "team", "public", "shared"})

_NAMESPACE_PREFIX = "memora://"
_MAX_SEGMENT_LEN = 128
_MAX_PATH_LEN = 1024


class AgentDelegationError(ValueError):
    """Raised when a parent tries to delegate outside its own authority."""


class IdentityService:
    # ------------------------------------------------------------------
    # Namespace path validation
    # ------------------------------------------------------------------
    @staticmethod
    def validate_namespace_path(path: str) -> str:
        """Normalise a namespace path and reject anything malformed.

        Namespace paths were accepted verbatim, so `memora://../../etc/passwd`
        was persisted as a real row. Paths are logical identifiers rather than
        filesystem paths, so that alone was data hygiene — but they are also the
        operand of prefix comparisons (a sub-agent's bounded_scope is matched
        with `namespace.path.startswith(bounded_scope)`), so a traversal segment
        can make a path that reads as belonging elsewhere satisfy a scope check.

        Returns the normalised `memora://`-prefixed path.
        """
        if not isinstance(path, str):
            raise ValueError("Namespace path must be a string.")
        candidate = path.strip()
        if not candidate:
            raise ValueError("Namespace path must not be empty.")
        if len(candidate) > _MAX_PATH_LEN:
            raise ValueError(f"Namespace path exceeds {_MAX_PATH_LEN} characters.")

        if not candidate.startswith(_NAMESPACE_PREFIX):
            candidate = f"{_NAMESPACE_PREFIX}{candidate.lstrip('/')}"

        body = candidate[len(_NAMESPACE_PREFIX):]
        if not body:
            raise ValueError(f"Namespace path '{path}' has no segments after 'memora://'.")

        for segment in body.split("/"):
            if segment in ("", ".", ".."):
                raise ValueError(
                    f"Namespace path '{path}' is malformed: segments must be non-empty "
                    "and may not be '.' or '..'."
                )
            if len(segment) > _MAX_SEGMENT_LEN:
                raise ValueError(
                    f"Namespace path '{path}' has a segment longer than "
                    f"{_MAX_SEGMENT_LEN} characters."
                )
        return candidate

    @staticmethod
    def namespace_root(path: str) -> Optional[str]:
        """The agent that owns `path`, or None for a shared root.

        `memora://forge/private` -> "forge" (owned by the forge agent)
        `memora://team/shared`   -> None   (shared space, no single owner)
        """
        body = path[len(_NAMESPACE_PREFIX):] if path.startswith(_NAMESPACE_PREFIX) else path.lstrip("/")
        root = body.split("/")[0]
        return None if root in OPEN_NAMESPACE_ROOTS else root

    @staticmethod
    def register_agent(
        db: Session,
        name: str,
        description: Optional[str] = None,
        role: str = "worker",
        parent_agent_id: Optional[str] = None,
        bounded_scope: Optional[str] = None,
        tenant_id: str = "default"
    ) -> Agent:
        agent_name = name.lower()
        agent = db.query(Agent).filter(Agent.name == agent_name, Agent.tenant_id == tenant_id).first()
        if not agent:
            agent = Agent(
                tenant_id=tenant_id,
                name=agent_name,
                description=description,
                role=role,
                parent_agent_id=parent_agent_id,
                bounded_scope=bounded_scope
            )
            db.add(agent)
            try:
                db.commit()
            except IntegrityError:
                # Lost a race with a concurrent registration of the same
                # (tenant_id, name). The composite unique index did its job;
                # roll back and adopt the row the other request won with
                # instead of surfacing a 400 to the caller.
                db.rollback()
                agent = db.query(Agent).filter(
                    Agent.name == agent_name, Agent.tenant_id == tenant_id
                ).first()
                if agent is None:
                    raise
            else:
                db.refresh(agent)

            # Create default private namespace for the agent if not bounded sub-agent
            if not bounded_scope:
                private_path = f"memora://{agent.name}/private"
                IdentityService.create_namespace(
                    db,
                    path=private_path,
                    agent_id=agent.id,
                    ns_type=NamespaceType.AGENT_PRIVATE,
                    tenant_id=tenant_id
                )
            else:
                # Subagent gets access to its bounded scope namespace
                IdentityService.resolve_namespace(db, bounded_scope, default_type=NamespaceType.PROJECT_PRIVATE, tenant_id=tenant_id)
        return agent

    @staticmethod
    def register_subagent(
        db: Session,
        parent_agent_name: str,
        subagent_name: str,
        bounded_scope: str,
        description: Optional[str] = None,
        tenant_id: str = "default"
    ) -> Agent:
        parent = IdentityService.get_agent_by_name(
            db, parent_agent_name, tenant_id=tenant_id
        )
        if parent is None:
            raise AgentDelegationError("The delegating agent is not registered in the requested tenant.")
        resolved_tenant = parent.tenant_id

        try:
            normalized_scope = IdentityService.validate_namespace_path(bounded_scope)
        except ValueError as exc:
            raise AgentDelegationError(str(exc)) from exc

        target_ns = IdentityService.get_namespace_by_path(
            db, normalized_scope, tenant_id=resolved_tenant
        )
        if target_ns is None:
            # Creating a scope implicitly grants its child access to it. Only the
            # parent (or the Memora service administrator) may mint a new scope;
            # otherwise a caller could name another agent's project and have this
            # trusted service create a grant that the caller could not grant itself.
            root = IdentityService.namespace_root(normalized_scope)
            if parent.name != "memora" and root != parent.name:
                raise AgentDelegationError(
                    "A delegator may create a bounded namespace only under its own namespace root."
                )
            target_ns = IdentityService.resolve_namespace(
                db,
                normalized_scope,
                default_type=NamespaceType.PROJECT_PRIVATE,
                owner_agent_id=parent.id,
                tenant_id=resolved_tenant,
            )

        if target_ns.type == NamespaceType.AGENT_PRIVATE:
            raise AgentDelegationError(
                "Sub-agents cannot be delegated into an agent-private namespace; use a project scope."
            )

        # Delegation cannot widen the parent's effective capabilities. Evaluate
        # the exact read/query/write actions against the scope, then grant only
        # the subset the parent already holds.
        from core.policy.engine import PolicyEngine

        delegated_actions = [
            action
            for action in ("read", "query", "write")
            if PolicyEngine.evaluate_access(
                db,
                actor=parent,
                namespace=target_ns,
                action=action,
                log_audit=False,
            ).allowed
        ]
        if not delegated_actions:
            raise AgentDelegationError(
                "The delegator has no read, query, or write authority over the requested scope."
            )

        formatted_subname = f"{parent.name}:{subagent_name.strip().lower()}"
        existing_child = IdentityService.get_agent_by_name(
            db, formatted_subname, tenant_id=resolved_tenant
        )
        if existing_child and (
            existing_child.parent_agent_id != parent.id
            or existing_child.bounded_scope != normalized_scope
        ):
            raise AgentDelegationError(
                "The sub-agent name is already registered with a different parent or bounded scope."
            )

        subagent = existing_child or IdentityService.register_agent(
            db,
            name=formatted_subname,
            description=description or f"Sub-agent of {parent.name} bounded to {normalized_scope}",
            role="subagent",
            parent_agent_id=parent.id,
            bounded_scope=normalized_scope,
            tenant_id=resolved_tenant,
        )
        if (
            subagent.parent_agent_id != parent.id
            or subagent.bounded_scope != normalized_scope
        ):
            raise AgentDelegationError(
                "The sub-agent name was concurrently registered with different delegation bounds."
            )

        IdentityService.grant_access(
            db,
            agent_id=subagent.id,
            namespace_id=target_ns.id,
            actions=delegated_actions,
            purpose=f"Bounded sub-agent access for {normalized_scope}",
            tenant_id=resolved_tenant,
        )
        return subagent

    @staticmethod
    def get_agent_by_name(
        db: Session,
        name: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Agent]:
        """Resolve an agent by name.

        Agent names are unique per tenant, not globally. When `tenant_id` is
        supplied the lookup is scoped to it; when it is omitted the historical
        global behaviour is preserved so the many existing call sites keep
        working. Callers that have an authenticated tenant in hand should pass
        it, otherwise an agent name owned by another tenant can be resolved.
        """
        query = db.query(Agent).filter(Agent.name == name.lower())
        if tenant_id is not None:
            query = query.filter(Agent.tenant_id == tenant_id)
        return query.first()

    @staticmethod
    def get_agent_by_id(
        db: Session,
        agent_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Agent]:
        query = db.query(Agent).filter(Agent.id == agent_id)
        if tenant_id is not None:
            query = query.filter(Agent.tenant_id == tenant_id)
        return query.first()

    @staticmethod
    def list_agents(db: Session, tenant_id: str = "default") -> List[Agent]:
        return db.query(Agent).filter(
            Agent.tenant_id == tenant_id
        ).order_by(Agent.name).all()

    @staticmethod
    def create_namespace(
        db: Session,
        path: str,
        ns_type: NamespaceType,
        agent_id: Optional[str] = None,
        tenant_id: str = "default"
    ) -> Namespace:
        path = IdentityService.validate_namespace_path(path)

        if agent_id is not None and IdentityService.get_agent_by_id(
            db, agent_id, tenant_id=tenant_id
        ) is None:
            raise ValueError("Namespace owner must be registered in the same tenant.")

        existing = db.query(Namespace).filter(Namespace.path == path, Namespace.tenant_id == tenant_id).first()
        if existing:
            return existing

        namespace = Namespace(
            tenant_id=tenant_id,
            path=path,
            type=ns_type,
            agent_id=agent_id
        )
        db.add(namespace)
        try:
            db.commit()
        except IntegrityError:
            # Same check-then-insert race as register_agent: a concurrent caller
            # created this (tenant_id, path) first. Adopt their row.
            db.rollback()
            winner = db.query(Namespace).filter(
                Namespace.path == path, Namespace.tenant_id == tenant_id
            ).first()
            if winner is None:
                raise
            return winner
        db.refresh(namespace)
        return namespace

    @staticmethod
    def resolve_namespace(
        db: Session,
        path: str,
        default_type: NamespaceType = NamespaceType.PROJECT_PRIVATE,
        owner_agent_id: Optional[str] = None,
        tenant_id: str = "default"
    ) -> Namespace:
        path = IdentityService.validate_namespace_path(path)

        ns = db.query(Namespace).filter(Namespace.path == path, Namespace.tenant_id == tenant_id).first()
        if ns:
            return ns

        segments = path[len(_NAMESPACE_PREFIX):].split("/")
        root = segments[0]
        ns_type = default_type
        # Open types are determined by canonical namespace roots, never by a
        # substring in an agent-owned project name. For example,
        # ``memora://friday/projects/publicity`` must remain private; the old
        # ``'/public' in path`` test silently made it openly readable. Likewise,
        # a personal path containing ``global`` must not acquire global access.
        if root == "public":
            ns_type = NamespaceType.PUBLIC
        elif root == "universe" and len(segments) >= 2 and segments[1] == "global":
            ns_type = NamespaceType.UNIVERSE_GLOBAL
        elif root in {"team", "shared"} or "team" in segments or "shared" in segments:
            ns_type = NamespaceType.TEAM_SHARED
        elif "private" in segments:
            ns_type = NamespaceType.AGENT_PRIVATE
        elif len(segments) >= 2 and segments[1] == "projects":
            ns_type = NamespaceType.PROJECT_PRIVATE

        return IdentityService.create_namespace(
            db,
            path=path,
            ns_type=ns_type,
            agent_id=owner_agent_id,
            tenant_id=tenant_id
        )

    @staticmethod
    def get_namespace_by_path(db: Session, path: str, tenant_id: str = "default") -> Optional[Namespace]:
        if not path.startswith("memora://"):
            path = f"memora://{path.lstrip('/')}"
        return db.query(Namespace).filter(Namespace.path == path, Namespace.tenant_id == tenant_id).first()

    @staticmethod
    def list_namespaces(db: Session, agent_id: Optional[str] = None, tenant_id: str = "default") -> List[Namespace]:
        query = db.query(Namespace).filter(Namespace.tenant_id == tenant_id)
        if agent_id:
            query = query.filter((Namespace.agent_id == agent_id) | (Namespace.type.in_([NamespaceType.UNIVERSE_GLOBAL, NamespaceType.PUBLIC])))
        return query.all()

    @staticmethod
    def grant_access(
        db: Session,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        namespace_id: Optional[str] = None,
        actions: Optional[List[str]] = None,
        purpose: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        ttl_hours: Optional[int] = None,
        tenant_id: str = "default",
        commit: bool = True,
    ) -> AccessGrant:
        namespace = db.query(Namespace).filter(
            Namespace.id == namespace_id,
            Namespace.tenant_id == tenant_id,
        ).first()
        if namespace is None:
            raise ValueError("Namespace does not exist in the requested tenant.")

        requested_actions = ["read", "query"] if actions is None else actions
        action_list = list(dict.fromkeys(
            str(action).strip().lower() for action in requested_actions
        ))
        allowed_actions = {"read", "query", "write"}
        if not action_list or any(action not in allowed_actions for action in action_list):
            raise ValueError(
                "Access grants may contain only read, query, and write actions; "
                "wildcard and lifecycle actions are forbidden."
            )
        if ttl_hours is not None and not 1 <= ttl_hours <= 24 * 365:
            raise ValueError("Grant TTL must be between 1 and 8760 hours.")

        if not agent_id and agent_name:
            # Scope to the caller's tenant. Agent names are unique per tenant, so
            # an unscoped lookup could attach this grant to an identically named
            # agent belonging to a different tenant.
            agent = IdentityService.get_agent_by_name(db, agent_name, tenant_id=tenant_id)
            if not agent:
                raise ValueError("Target agent is not registered in the requested tenant.")
            resolved_agent_id = agent.id
        elif agent_id:
            # Resolve only within the grant's tenant; an ID/name lookup must not
            # attach a namespace grant to an identically named foreign actor.
            agent = IdentityService.get_agent_by_id(db, agent_id)
            if agent and agent.tenant_id != tenant_id:
                raise ValueError("Target agent belongs to a different tenant.")
            if not agent:
                agent = IdentityService.get_agent_by_name(db, agent_id, tenant_id=tenant_id)
            if not agent:
                raise ValueError("Target agent is not registered in the requested tenant.")
            resolved_agent_id = agent.id
        else:
            raise ValueError("Either agent_id or agent_name must be provided.")

        if ttl_hours and not expires_at:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

        grant = db.query(AccessGrant).filter(
            AccessGrant.tenant_id == tenant_id,
            AccessGrant.agent_id == resolved_agent_id,
            AccessGrant.namespace_id == namespace_id
        ).first()

        if grant:
            grant.actions = action_list
            grant.purpose = purpose
            # Only move the expiry when the caller actually specified one. The
            # unconditional assignment silently converted a time-boxed grant into
            # a permanent one whenever someone re-granted to change the action
            # list, which is a privilege upgrade nobody asked for.
            if expires_at is not None:
                grant.expires_at = expires_at
        else:
            grant = AccessGrant(
                tenant_id=tenant_id,
                agent_id=resolved_agent_id,
                namespace_id=namespace_id,
                actions=action_list,
                purpose=purpose,
                expires_at=expires_at
            )
            db.add(grant)
        if commit:
            db.commit()
        else:
            db.flush()
        db.refresh(grant)
        return grant

    @staticmethod
    def revoke_access(
        db: Session,
        agent_id: str,
        namespace_id: str,
        tenant_id: Optional[str] = None,
        commit: bool = True,
    ) -> bool:
        """Revoke a grant.

        `tenant_id` scopes the lookup so a caller cannot revoke a grant that
        belongs to another tenant. Omitted preserves the previous global
        behaviour for existing callers.
        """
        query = db.query(AccessGrant).filter(
            AccessGrant.agent_id == agent_id,
            AccessGrant.namespace_id == namespace_id
        )
        if tenant_id is not None:
            query = query.filter(AccessGrant.tenant_id == tenant_id)
        grant = query.first()
        if grant:
            db.delete(grant)
            if commit:
                db.commit()
            else:
                db.flush()
            return True
        return False