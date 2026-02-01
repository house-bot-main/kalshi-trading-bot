"""Market making strategy - captures bid-ask spreads."""
from typing import Dict, Optional, List
from datetime import datetime
from .base import BaseStrategy, Signal

class MarketMakingStrategy(BaseStrategy):
    """Provide liquidity, capture bid-ask spread."""
    
    def __init__(self, config: Dict):
        super().__init__("MarketMaking", config)
        self.min_spread = config.get("min_spread", 0.05)
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        market_id = market_data.get("market_id")
        order_book = market_data.get("order_book", {})
        best_bid = order_book.get("best_bid", 0)
        best_ask = order_book.get("best_ask", 1.0)
        
        spread = best_ask - best_bid
        if spread < self.min_spread or best_bid == 0:
            return None
        
        return Signal(
            market_id=market_id,
            side="both",
            confidence=spread,
            size=self.config.get("position_size", 5.0),
            strategy_name=self.name,
            timestamp=datetime.now(),
            metadata={"spread": spread, "mid": (best_bid + best_ask) / 2}
        )
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        return False  # Simplified for now
