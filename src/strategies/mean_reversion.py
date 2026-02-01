"""Mean reversion strategy - fades extreme prices."""
from typing import Dict, Optional
from datetime import datetime
from .base import BaseStrategy, Signal

class MeanReversionStrategy(BaseStrategy):
    """
    Strategy: Buy when prices are extreme (mean reversion).
    
    Logic:
    - If YES > 95¢, sell YES (expect reversion down)
    - If YES < 5¢, buy YES (expect reversion up)
    - Exit when price normalizes or at resolution
    """
    
    def __init__(self, config: Dict):
        super().__init__("MeanReversion", config)
        self.extreme_threshold = config.get("extreme_threshold", 0.95)
        self.min_threshold = config.get("min_threshold", 0.05)
        self.exit_target = config.get("exit_target", 0.50)  # Exit at 50%
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Look for extreme prices to fade."""
        market_id = market_data.get("market_id")
        yes_price = market_data.get("yes_price", 0.5)
        no_price = 1.0 - yes_price
        
        # Check for extreme YES price
        if yes_price >= self.extreme_threshold:
            # Price too high, expect reversion down
            return Signal(
                market_id=market_id,
                side="no",  # Bet against YES
                confidence=min(0.95, yes_price),
                size=self._calculate_position_size(yes_price),
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={"reason": "extreme_yes_price", "price": yes_price}
            )
        
        # Check for extreme NO price (low YES price)
        if yes_price <= self.min_threshold:
            # Price too low, expect reversion up
            return Signal(
                market_id=market_id,
                side="yes",  # Bet on YES
                confidence=min(0.95, 1.0 - yes_price),
                size=self._calculate_position_size(yes_price),
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={"reason": "extreme_no_price", "price": yes_price}
            )
        
        return None
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Exit when price normalizes toward 50%."""
        entry_price = position.get("entry_price", 0.5)
        current_price = market_data.get("yes_price", 0.5)
        
        # Exit if price moved toward 50% significantly
        if entry_price > 0.5 and current_price <= self.exit_target:
            return True
        if entry_price < 0.5 and current_price >= self.exit_target:
            return True
            
        # Also exit if market is closing soon
        if market_data.get("close_in_hours", 24) < 1:
            return True
            
        return False
    
    def _calculate_position_size(self, price: float) -> float:
        """Larger positions at more extreme prices."""
        distance_from_50 = abs(price - 0.5)
        base_size = self.config.get("base_position_size", 5.0)  # $5 default
        multiplier = 1.0 + (distance_from_50 * 2)  # 1x to 2x sizing
        return min(base_size * multiplier, self.config.get("max_position_size", 10.0))