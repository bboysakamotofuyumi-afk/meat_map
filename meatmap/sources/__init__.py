"""
Source specific ingestion clients.
"""

from .hotpepper import HotPepperClient, normalize_hotpepper_store

__all__ = [
    "HotPepperClient",
    "normalize_hotpepper_store",
]
