"""Momentum strategy - follows price trends."""
from typing import Dict, Optional, List
from datetime import datetime
from collections import deque
from .base import BaseStrategy, Signal

class MomentumStrategy(BaseStrategy):
    """
    Strategy: Follow price momentum (trend is your friend).
    
    Logic:
    - Track price history with moving averages
    - Enter when price breaks above/below MA with momentum
    - Exit on momentum reversal or time decay
    """
    
    def __init__(self, config: Dict):
        super().__init__("Momentum", config)
        self.short_ma_period = config.get("short_ma", 5)
        self.long_ma_period = config.get("long_ma", 20)
        self.momentum_threshold = config.get("momentum_threshold", 0.02)
        self.price_history: Dict[str, deque] = {}
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Look for momentum signals."""
        market_id = market_data.get("market_id")
        yes_price = market_data.get("yes_price")
        
        if yes_price is None:
            return None
        
        # Update price history
        if market_id not in self.price_history:
            self.price_history[market_id] = deque(maxlen=self.long_ma_period)
        self.price_history[market_id].append(yes_price)
        
        # Need enough data
        if len(self.price_history[market_id]) < self.long_ma_period:
            return None
        
        # Calculate moving averages
        prices = list(self.price_history[market_id])
        short_ma = sum(prices[-self.short_ma_period:]) / self.short_ma_period
        long_ma = sum(prices) / self.long_ma_period
        
        # Check for momentum
        momentum = short_ma - long_ma
        
        if momentum > self.momentum_threshold and yes_price > short_ma:
            # Upward momentum - buy YES
            return Signal(
                market_id=market_id,
                side="yes",
                confidence=min(0.9, 0.5 + abs(momentum)),
                size=self.config.get("position_size", 5.0),
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={
                    "reason": "upward_momentum",
                    "short_ma": short_ma,
                    "long_ma": long_ma,
                    "momentum": momentum
                }
            )
        
        if momentum < -self.momentum_threshold and yes_price < short_ma:
            # Downward momentum - buy NO (bet against YES)
            return Signal(
                market_id=market_id,
                side="no",
                confidence=min(0.9, 0.5 + abs(momentum)),
                size=self.config.get("position_size", 5.0),
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={
                    "reason": "downward_momentum",
                    "short_ma": short_ma,
                    "long_ma": long_ma,
                    "momentum": momentum
                }
            )
        
        return None
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Exit on momentum reversal."""
        market_id = market_data.get("market_id")
        entry_momentum = position.get("metadata", {}).get("momentum", 0)
        
        if market_id not in self.price_history:
            return False
        
        prices = list(self.price_history[market_id])
        if len(prices) < self.long_ma_period:
            return False
        
        short_ma = sum(prices[-self.short_ma_period:]) / self.short_ma_period
        long_ma = sum(prices) / self.long_ma_period
        current_momentum = short_ma - long_ma
        
        # Exit if momentum reversed
        if entry_momentum > 0 and current_momentum < 0:
            return True
        if entry_momentum < 0 and current_momentum > 0:
            return True
        
        # Exit if market closing
        if market_data.get("close_in_hours", 24) < 2:
            return True
            
        return False