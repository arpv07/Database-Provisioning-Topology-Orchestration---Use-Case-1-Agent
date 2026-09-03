"""
Entry point – run with:
  uvicorn oracle_provisioner.server:app --reload --port 8000
"""

from oracle_provisioner.backend.app import app  # re-export

__all__ = ["app"]
