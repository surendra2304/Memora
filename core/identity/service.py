"""
Identity and Namespace Resolution Service
Manages registered ecosystem agents, parent-subagent delegation with bounded contexts,
and dynamic URI namespace resolution.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from storage.relational.models import Agent, Namespace, NamespaceType, AccessGrant

class IdentityService:
    @staticmethod
    def register_agent(
        db: Session,
        name: str,
        description: Optional[str] = None,
        role: str = "worker",
        parent_agent_id: Optional[str] = None,
        bounded_scope: Optional[str] = None
    ) -> Agent:
        agent_name = name.lower()
        agent = db.query(Agent).filter(Agent.name == agent_name).first()
        if not agent:
            agent = Agent(
                name=agent_name,
                description=description,
                role=role,
                parent_agent_id=parent_agent_id,
                bounded_scope=bounded_scope
            )
            db.add(agent)
            db.commit()
            db.refresh(agent)

            # Create default private namespace for the agent if not bounded sub-agent
            if not bounded_scope:
                private_path = f"memora://{agent.name}/private"
                IdentityService.create_namespace(
                    db,
                    path=private_path,
                    agent_id=agent.id,
                    ns_type=NamespaceType.AGENT_PRIVATE
                )
            else:
                # Subagent gets access to its bounded scope namespace
                IdentityService.resolve_namespace(db, bounded_scope, default_type=NamespaceType.PROJECT_PRIVATE)
        return agent

    @staticmethod
    def register_subagent(
        db: Session,
        parent_agent_name: str,
        subagent_name: str,
        bounded_scope: str,
        description: Optional[str] = None
    ) -> Agent:
        """
        Creates a sub-agent with bounded inherited context.
        The sub-agent can only access its specific bounded_scope (e.g. 'memora://forge/projects/app-17'),
        never the parent's full private namespace.
        """
        parent = IdentityService.get_agent_by_name(db, parent_agent_name)
        if not parent:
            parent = IdentityService.register_agent(db, parent_agent_name)

        if not bounded_scope.startswith("memora://"):
            bounded_scope = f"memora://{bounded_scope.lstrip('/')}"

        # Subagent name scoping
        formatted_subname = f"{parent.name}:{subagent_name.lower()}"
        subagent = IdentityService.register_agent(
            db,
            name=formatted_subname,
            description=description or f"Sub-agent of {parent.name} bounded to {bounded_scope}",
            role="subagent",
            parent_agent_id=parent.id,
            bounded_scope=bounded_scope
        )

        # Grant access to the bounded namespace
        target_ns = IdentityService.resolve_namespace(db, bounded_scope, default_type=NamespaceType.PROJECT_PRIVATE)
        IdentityService.grant_access(
            db,
            agent_id=subagent.id,
            namespace_id=target_ns.id,
            actions=["read", "write", "query"],
            purpose=f"Bounded subtask execution for parent {parent.name}"
        )
        return subagent

    @staticmethod
    def get_agent_by_name(db: Session, name: str) -> Optional[Agent]:
        return db.query(Agent).filter(Agent.name == name.lower()).first()

    @staticmethod
    def get_agent_by_id(db: Session, agent_id: str) -> Optional[Agent]:
        return db.query(Agent).filter(Agent.id == agent_id).first()

    @staticmethod
    def list_agents(db: Session) -> List[Agent]:
        return db.query(Agent).order_by(Agent.name).all()

    @staticmethod
    def create_namespace(
        db: Session,
        path: str,
        ns_type: NamespaceType,
        agent_id: Optional[str] = None
    ) -> Namespace:
        if not path.startswith("memora://"):
            path = f"memora://{path.lstrip('/')}"

        existing = db.query(Namespace).filter(Namespace.path == path).first()
        if existing:
            return existing

        namespace = Namespace(
            path=path,
            type=ns_type,
            agent_id=agent_id
        )
        db.add(namespace)
        db.commit()
        db.refresh(namespace)
        return namespace

    @staticmethod
    def resolve_namespace(
        db: Session,
        path: str,
        default_type: NamespaceType = NamespaceType.PROJECT_PRIVATE,
        owner_agent_id: Optional[str] = None
    ) -> Namespace:
        """Resolves a namespace URI path, creating it if it does not exist."""
        if not path.startswith("memora://"):
            path = f"memora://{path.lstrip('/')}"

        ns = db.query(Namespace).filter(Namespace.path == path).first()
        if ns:
            return ns

        # Auto-infer namespace type from path pattern if not explicitly set
        ns_type = default_type
        if "/private" in path:
            ns_type = NamespaceType.AGENT_PRIVATE
        elif "/global" in path or path == "memora://universe/global":
            ns_type = NamespaceType.UNIVERSE_GLOBAL
        elif "/shared" in path or "/team" in path:
            ns_type = NamespaceType.TEAM_SHARED
        elif "/public" in path:
            ns_type = NamespaceType.PUBLIC
        elif "/projects/" in path:
            ns_type = NamespaceType.PROJECT_PRIVATE

        return IdentityService.create_namespace(
            db,
            path=path,
            ns_type=ns_type,
            agent_id=owner_agent_id
        )

    @staticmethod
    def get_namespace_by_path(db: Session, path: str) -> Optional[Namespace]:
        if not path.startswith("memora://"):
            path = f"memora://{path.lstrip('/')}"
        return db.query(Namespace).filter(Namespace.path == path).first()

    @staticmethod
    def list_namespaces(db: Session, agent_id: Optional[str] = None) -> List[Namespace]:
        query = db.query(Namespace)
        if agent_id:
            query = query.filter((Namespace.agent_id == agent_id) | (Namespace.type.in_([NamespaceType.UNIVERSE_GLOBAL, NamespaceType.PUBLIC])))
        return query.all()

    @staticmethod
    def grant_access(
        db: Session,
        agent_id: str,
        namespace_id: str,
        actions: Optional[List[str]] = None,
        purpose: Optional[str] = None,
        expires_at: Optional[datetime] = None
    ) -> AccessGrant:
        grant = db.query(AccessGrant).filter(
            AccessGrant.agent_id == agent_id,
            AccessGrant.namespace_id == namespace_id
        ).first()

        action_list = actions or ["read", "query"]
        if grant:
            grant.actions = action_list
            grant.purpose = purpose
            grant.expires_at = expires_at
        else:
            grant = AccessGrant(
                agent_id=agent_id,
                namespace_id=namespace_id,
                actions=action_list,
                purpose=purpose,
                expires_at=expires_at
            )
            db.add(grant)
        db.commit()
        db.refresh(grant)
        return grant

    @staticmethod
    def revoke_access(db: Session, agent_id: str, namespace_id: str) -> bool:
        grant = db.query(AccessGrant).filter(
            AccessGrant.agent_id == agent_id,
            AccessGrant.namespace_id == namespace_id
        ).first()
        if grant:
            db.delete(grant)
            db.commit()
            return True
        return False