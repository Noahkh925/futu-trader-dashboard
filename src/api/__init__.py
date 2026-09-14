"""Read-only console API (T3 Stage 3). No live-enable endpoints."""

from api.app import create_app

__all__ = ["create_app"]
