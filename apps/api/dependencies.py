"""
FastAPI Dependencies for Memora API
"""
import hmac
import os
from typing import Optional
from fastapi import Header, HTTPException, status
from core.config import settings

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
    key_names = {
        "friday": "FRIDAY_API_KEY",
        "inference": "INFERENCE_API_KEY",
        "stratex": "STRATEX_API_KEY",
        "intelx": "INTELX_API_KEY",
        "futuris": "FUTURIS_API_KEY",
        "cortex": "CORTEX_API_KEY",
        "forge": "FORGE_API_KEY",
        "sentinel": "SENTINEL_API_KEY",
        "memora": "MEMORA_API_KEY",
    }
    supplied = x_api_key
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    expected = os.getenv(key_names.get(agent, ""), "") if agent in key_names else ""
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
        if agent not in key_names or not expected:
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
