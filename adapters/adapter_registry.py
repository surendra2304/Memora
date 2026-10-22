"""
Adapter Registry for Memora Ecosystem
Maps agent names to their respective configured adapters and default namespaces.
"""
from typing import Dict, Any, Optional, List, Type
from pathlib import Path
import yaml
import logging

from adapters.base_adapter import BaseAgentAdapter

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent / "adapter_config.yaml"

FALLBACK_CONFIG = {
    "server": {"base_url": "http://localhost:8000", "timeout_seconds": 10},
    "agents": {
        "friday": {"role": "supervisor", "default_namespace": "memora://friday/private", "default_token_budget": 8000},
        "forge": {"role": "worker", "default_namespace": "memora://forge/private", "default_token_budget": 4000},
        "futuris": {"role": "forecaster", "default_namespace": "memora://futuris/private", "default_token_budget": 4000},
        "intelx": {"role": "researcher", "default_namespace": "memora://intelx/private", "default_token_budget": 6000},
        "mt5": {"role": "trader", "default_namespace": "memora://mt5/private", "default_token_budget": 3000},
        "nexus": {"role": "interface", "default_namespace": "memora://nexus/private", "default_token_budget": 4000},
        "sentinel": {"role": "security", "default_namespace": "memora://sentinel/private", "default_token_budget": 4000},
        "ai_universe": {"role": "global", "default_namespace": "memora://universe/global", "default_token_budget": 8000},
    }
}

class AdapterRegistry:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self._config: Dict[str, Any] = self._load_config()
        self._instances: Dict[str, BaseAgentAdapter] = {}
        self._custom_adapter_classes: Dict[str, Type[BaseAgentAdapter]] = {}

        # Lazy register specialized classes
        self._register_default_specialized_classes()

    def _register_default_specialized_classes(self):
        try:
            from adapters.friday.adapter import FridayAdapter
            self.register_custom_class("friday", FridayAdapter)
        except ImportError:
            pass

        try:
            from adapters.ai_universe.adapter import AIUniverseAdapter
            self.register_custom_class("ai_universe", AIUniverseAdapter)
        except ImportError:
            pass

        try:
            from adapters.forge.adapter import ForgeAdapter
            self.register_custom_class("forge", ForgeAdapter)
        except ImportError:
            pass

        try:
            from adapters.sentinel.adapter import SentinelAdapter
            self.register_custom_class("sentinel", SentinelAdapter)
        except ImportError:
            pass

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict) and "agents" in data:
                        return data
            except Exception as e:
                logger.warning(f"Failed to load adapter config from {self.config_path}: {e}. Using fallback.")
        return FALLBACK_CONFIG

    def register_custom_class(self, agent_name: str, adapter_cls: Type[BaseAgentAdapter]):
        """Registers a custom subclass of BaseAgentAdapter for an agent."""
        self._custom_adapter_classes[agent_name.lower()] = adapter_cls

    def register_adapter(self, agent_name: str, adapter_instance: BaseAgentAdapter):
        """Registers a pre-instantiated adapter instance in the registry."""
        self._instances[agent_name.lower()] = adapter_instance

    def get_agent_config(self, agent_name: str) -> Dict[str, Any]:
        """Returns the configuration dict for a specific agent."""
        agents = self._config.get("agents", {})
        return agents.get(agent_name.lower(), {
            "role": "worker",
            "default_namespace": f"memora://{agent_name.lower()}/private",
            "default_token_budget": 4000
        })

    def list_configured_agents(self) -> List[str]:
        """Returns the list of all configured agent names."""
        return list(self._config.get("agents", {}).keys())

    def get_adapter(
        self,
        agent_name: str,
        http_client: Optional[Any] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> BaseAgentAdapter:
        """
        Retrieves or creates a configured BaseAgentAdapter instance for the specified agent.
        """
        normalized_name = agent_name.lower()
        if normalized_name in self._instances and not http_client:
            return self._instances[normalized_name]

        cfg = self.get_agent_config(normalized_name)
        server_cfg = self._config.get("server", {})
        resolved_base_url = base_url or server_cfg.get("base_url", "http://localhost:8000")

        adapter_cls = self._custom_adapter_classes.get(normalized_name, BaseAgentAdapter)
        adapter = adapter_cls(
            base_url=resolved_base_url,
            api_key=api_key,
            default_namespace=cfg.get("default_namespace", f"memora://{normalized_name}/private"),
            http_client=http_client
        ) if adapter_cls != BaseAgentAdapter else adapter_cls(
            agent_name=normalized_name,
            base_url=resolved_base_url,
            api_key=api_key,
            default_namespace=cfg.get("default_namespace", f"memora://{normalized_name}/private"),
            role=cfg.get("role", "worker"),
            http_client=http_client
        )

        if not http_client:
            self._instances[normalized_name] = adapter
        return adapter

# Singleton Registry instance
adapter_registry = AdapterRegistry()