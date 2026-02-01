"""Kalshi Trading Bot - Multi-strategy paper trading system."""
from .config import Config
from .scanner import MarketScanner
from .paper_trader import PaperTrader
from .performance import PerformanceTracker
from .allocator import CapitalAllocator
from .orchestrator import Orchestrator

__version__ = "0.1.0"

__all__ = [
    "Config",
    "MarketScanner", 
    "PaperTrader",
    "PerformanceTracker",
    "CapitalAllocator",
    "Orchestrator",
]
