"""Base strategy interface for Kalshi trading bot."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime

@dataclass
class Signal:
    """Trading signal from a strategy."""
    market_id: str
    side: str  # 'yes' or 'no'
    confidence: float  # 0.0 to 1.0
    size: float  # Position size
    strategy_name: str
    timestamp: datetime
    metadata: Optional[Dict] = None

class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""
    
    def __init__(self, name: str, config: Dict):
        self.name = name
        self.config = config
        self.active = True
        self.paper_mode = True  # Default to paper trading
        
    @abstractmethod
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Analyze market data and return signal if opportunity exists."""
        pass
    
    @abstractmethod
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Determine if current position should be closed."""
        pass
    
    def enable_paper_mode(self):
        """Enable paper trading (no real money)."""
        self.paper_mode = True
        
    def disable_paper_mode(self):
        """Enable live trading (real money)."""
        self.paper_mode = False