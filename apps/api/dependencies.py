"""
FastAPI Dependencies for Memora API
"""
import hmac
import os
from typing import Optional
from fastapi import Header, HTTPException, status
from core.config import settings

AGENT_API_KEY_ENV_VARS = {
    "friday": "FRIDAY_API_KEY",
    "inference": "INFERENCE_API_KEY",
    "stratex": "STRATEX_API_KEY",
    "intelx": "INTELX_API_KEY",
    "futuris": "FUTURIS_API_KEY",
    "cortex": "CORTEX_API_KEY",
    "forge": "FORGE_API_KEY",
    "sentinel": "SENTINEL_API_KEY",
    "ai_universe": "AI_UNIVERSE_API_KEY",
    "memora": "MEMORA_API_KEY",
}


def get_actor_header(
    x_agent_name: Optional[str] = Header(default=None, alias="X-Agent-Name"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> str:
    """Return the authenticated agent identity, never an unverified header claim."""
    return authenticate_agent(x_agent_name, x_api_key, authorization)

def get_purpose_header(
    x_access_purpose: Optional[str] = Header(default=None, alias="X-Access-Purpose")
) -> Optional[str]:
    """Extracts the stated purpose / intent for audit and policy verification."""
    return x_access_purpose


def resolve_agent_selector(
    db,
    authenticated_name: str,
    requested_name: Optional[str],
    *,
    allow_direct_subagent: bool = False,
) -> str:
    """Bind a body agent selector to the authenticated principal.

    Most routes must use the authenticated agent only. The context route is the
    one exception: a parent may build context for one of its registered direct
    children, but never for an unrelated agent. The child's bounded scope is then
    enforced by the normal policy-filtered retrieval path.
    """
    principal = (authenticated_name or "").strip().lower()
    requested = str(requested_name or "").strip()

    # Import locally to keep the HTTP dependency module lightweight and to avoid
    # making identity/model imports part of authentication's import cycle.
    from core.identity.service import IdentityService
    from storage.relational.models import Agent

    # API credentials do not yet carry a tenant claim. Resolve the credential's
    # own principal in the API-bound tenant even when the body omits an agent
    # selector; otherwise downstream services can resolve the same name globally
    # and bind the request to a same-named identity in a foreign tenant.
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="An authenticated agent identity is required.",
        )
    actor = IdentityService.get_agent_by_name(db, principal, tenant_id="default")
    if actor is None:
        # The credential has already authenticated the principal. Bootstrap only
        # that exact principal into the API-bound tenant rather than adopting a
        # same-named row from elsewhere. This also preserves the SDK's first-write
        # workflow while keeping tenant selection unambiguous.
        actor = IdentityService.register_agent(
            db, principal, role="worker", tenant_id="default"
        )

    if not requested or requested.lower() == principal:
        return actor.name

    if requested == actor.id:
        return actor.name

    candidate = db.query(Agent).filter(
        Agent.tenant_id == actor.tenant_id,
        Agent.id == requested,
    ).first()
    if candidate is None:
        candidate = db.query(Agent).filter(
            Agent.tenant_id == actor.tenant_id,
            Agent.name == requested.lower(),
        ).first()

    if (
        allow_direct_subagent
        and candidate is not None
        and candidate.parent_agent_id == actor.id
        and candidate.bounded_scope
    ):
        return candidate.name

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="The requested agent does not match the authenticated identity or an authorized direct sub-agent.",
    )


#: Agents permitted to perform fabric-wide administration.
#:
#: Identity registration is the root of every policy decision in Memora, and the
#: audit trail records every actor and denial reason across the mesh. Routers
#: that expose those were authenticated but not authorised, so any agent holding
#: a valid credential could mint a new identity — including one with
#: role="supervisor" — and read other agents' audit entries. Verifying against a
#: live server: intelx created agent 'rogue' with role=supervisor (HTTP 201) and
#: read the whole audit log (HTTP 200).
ADMIN_AGENTS = {"memora"}


def require_admin(actor_name: str) -> str:
    """Raise 403 unless the caller may administer the fabric."""
    if actor_name not in ADMIN_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Agent '{actor_name}' may not perform fabric administration. "
                f"Restricted to: {', '.join(sorted(ADMIN_AGENTS))}."
            ),
        )
    return actor_name


def authenticate_agent(
    x_agent_name: Optional[str] = Header(default=None, alias="X-Agent-Name"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> str:
    """Authenticate an ecosystem agent using its named service credential."""
    agent = (x_agent_name or "").strip().lower()
    supplied = x_api_key
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    expected = os.getenv(AGENT_API_KEY_ENV_VARS.get(agent, ""), "")
    production = str(getattr(settings, "MEMORA_ENV", "")).lower() == "production" or os.getenv("ENVIRONMENT", "").lower() == "production"

    # Authentication fails closed. The previous guard waved through any request that
    # carried no credential at all whenever the service was not flagged production,
    # so a deployment whose ENVIRONMENT value drifted or was lost silently accepted
    # unauthenticated writes into the memory every other agent reads. Every other agent
    # in the mesh rejects a missing credential outright, so this one did not either.
    anonymous_allowed = str(os.getenv("MEMORA_ALLOW_ANONYMOUS_DEV", "")).lower() in {"1", "true", "yes"}

    if not anonymous_allowed:
        if not agent or not supplied:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing agent credentials",
            )
        if agent not in AGENT_API_KEY_ENV_VARS or not expected:
            # A known caller whose key is absent is a configuration fault, not a
            # bad credential. Report it as such rather than as an authentication failure.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No configured credential for agent '{agent or 'unknown'}'",
            )
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent credentials")
        return agent

    # Opt-in anonymous access exists only for local development and tests.
    if not production and not expected and not supplied:
        return agent or "friday"
    if not agent or not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent credentials")
    return agent
