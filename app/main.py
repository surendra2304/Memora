"""Entrypoint shim delegating to apps.api.main for compatibility."""
from apps.api.main import app

__all__ = ["app"]
