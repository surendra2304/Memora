"""
Identity and Namespace Resolution Service
Manages registered ecosystem agents and resolves URI paths to namespaces.
"""
import re
from typing import Optional, List
from sqlalchemy.orm import Session
from storage.relational.models import Agent, Namespace, NamespaceType

class IdentityService:
    @staticmethod
    def register_agent(db: Session, name: str, description: Optional[str] = None) -> Agent:
        agent = db.query(Agent).filter(Agent.name == name.lower()).first()
        if not agent:
            agent = Agent(name=name.lower(), description=description)
            db.add(agent)
            db.commit()
            db.refresh(agent)
            # Create default private namespace for the agent
            private_path = f"memora://{agent.name}/private"
            IdentityService.create_namespace(
                db,
                path=private_path,
                agent_id=agent.id,
                ns_type=NamespaceType.AGENT_PRIVATE
            )
        return agent

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