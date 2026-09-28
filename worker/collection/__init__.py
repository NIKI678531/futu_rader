"""Automatic Futu collection behind one small worker-facing interface."""

from .models import AiRequest, AiResult, SyncRequest, SyncResult
from .service import FutuRefresh
from .source import MarketInsightMySqlAdapter, MemorySourceAdapter

__all__ = [
    "AiRequest",
    "AiResult",
    "FutuRefresh",
    "MarketInsightMySqlAdapter",
    "MemorySourceAdapter",
    "SyncRequest",
    "SyncResult",
]
