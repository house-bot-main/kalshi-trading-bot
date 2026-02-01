"""
UFC-specific trading strategies.

These strategies are tailored for prediction markets on UFC fights,
including main fight outcomes and prop bets.
"""
from typing import Dict, Optional
from datetime import datetime
from collections import defaultdict
from .base import BaseStrategy, Signal


class FavoriteBackerStrategy(BaseStrategy):
    """
    Strategy: Back heavy favorites in UFC fights.
    
    Logic:
    - If a fighter is priced >70%, they're a strong favorite
    - Favorites win ~75% of the time in UFC
    - Small edge betting on favorites at fair odds
    
    Best for: Main fight winner markets (KXUFCFIGHT)
    """
    
    def __init__(self, config: Dict):
        super().__init__("FavoriteBacker", config)
        self.min_favorite_price = config.get("min_favorite_price", 0.70)
        self.max_favorite_price = config.get("max_favorite_price", 0.85)
        self.position_size = config.get("position_size", 5.0)
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Look for favorites to back."""
        market_id = market_data.get("market_id", "")
        yes_price = market_data.get("yes_price", 0.5)
        
        # Only trade main fight outcomes (not props)
        if "KXUFCFIGHT" not in market_id:
            return None
        
        # Check if this is a favorite in our range
        if self.min_favorite_price <= yes_price <= self.max_favorite_price:
            return Signal(
                market_id=market_id,
                side="yes",
                confidence=yes_price,
                size=self.position_size,
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={"reason": "favorite_in_range", "price": yes_price}
            )
        
        return None
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Exit if price drops significantly or approaches resolution."""
        entry_price = position.get("entry_price", 0.5)
        current_price = market_data.get("yes_price", 0.5)
        
        # Stop loss: exit if price drops >15% from entry
        if current_price < entry_price * 0.85:
            return True
        
        # Take profit: exit if we've gained >20%
        if current_price > entry_price * 1.20:
            return True
        
        # Exit close to event
        if market_data.get("close_in_hours", 24) < 0.5:
            return True
        
        return False


class UnderdogValueStrategy(BaseStrategy):
    """
    Strategy: Find value in underdogs.
    
    Logic:
    - Underdogs priced 15-35% often have value
    - UFC is unpredictable, upsets happen ~25% of the time
    - Small bets on live underdogs can pay off
    
    Best for: Main fight winner markets
    """
    
    def __init__(self, config: Dict):
        super().__init__("UnderdogValue", config)
        self.min_underdog_price = config.get("min_underdog_price", 0.15)
        self.max_underdog_price = config.get("max_underdog_price", 0.35)
        self.position_size = config.get("position_size", 3.0)  # Smaller size for underdogs
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Look for underdog value."""
        market_id = market_data.get("market_id", "")
        yes_price = market_data.get("yes_price", 0.5)
        
        # Only trade main fight outcomes
        if "KXUFCFIGHT" not in market_id:
            return None
        
        # Check if this is an underdog in our range
        if self.min_underdog_price <= yes_price <= self.max_underdog_price:
            return Signal(
                market_id=market_id,
                side="yes",
                confidence=1.0 - yes_price,  # Higher confidence for bigger underdogs
                size=self.position_size,
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={"reason": "underdog_value", "price": yes_price}
            )
        
        return None
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Exit on big moves or near resolution."""
        entry_price = position.get("entry_price", 0.5)
        current_price = market_data.get("yes_price", 0.5)
        
        # Take profit: doubled our money
        if current_price >= entry_price * 2.0:
            return True
        
        # Stop loss: price dropped by half
        if current_price < entry_price * 0.5:
            return True
        
        # Exit close to event
        if market_data.get("close_in_hours", 24) < 0.5:
            return True
        
        return False


class FinishPropStrategy(BaseStrategy):
    """
    Strategy: Bet on fights ending by finish (KO/TKO or Submission).
    
    Logic:
    - ~55% of UFC fights end by finish (not decision)
    - If "finish before round X" props are cheap, bet on them
    - Works best for fights with two finishers
    
    Best for: KXUFCROUNDS markets
    """
    
    def __init__(self, config: Dict):
        super().__init__("FinishProp", config)
        self.max_finish_price = config.get("max_finish_price", 0.40)
        self.position_size = config.get("position_size", 4.0)
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Look for underpriced finish props."""
        market_id = market_data.get("market_id", "")
        yes_price = market_data.get("yes_price", 0.5)
        
        # Only trade round finish markets
        if "KXUFCROUNDS" not in market_id:
            return None
        
        # Look for finish props priced below our threshold
        # These are bets that the fight ends before round X
        if yes_price <= self.max_finish_price and yes_price > 0.05:
            return Signal(
                market_id=market_id,
                side="yes",
                confidence=0.55,  # Historical finish rate
                size=self.position_size,
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={"reason": "finish_value", "price": yes_price}
            )
        
        return None
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Exit based on price movement."""
        entry_price = position.get("entry_price", 0.5)
        current_price = market_data.get("yes_price", 0.5)
        
        # Take profit at 50% gain
        if current_price >= entry_price * 1.5:
            return True
        
        # Stop loss at 40% loss
        if current_price < entry_price * 0.6:
            return True
        
        # Exit close to event
        if market_data.get("close_in_hours", 24) < 0.25:
            return True
        
        return False


class MethodOfVictoryStrategy(BaseStrategy):
    """
    Strategy: Bet on specific methods of victory.
    
    Logic:
    - KO/TKO is most common finish (~30% of all fights)
    - Submission is less common (~20%)
    - Decision accounts for ~45%
    - Find mispriced MOV markets
    
    Best for: KXUFCMOV markets
    """
    
    def __init__(self, config: Dict):
        super().__init__("MethodOfVictory", config)
        self.ko_fair_price = config.get("ko_fair_price", 0.30)
        self.sub_fair_price = config.get("sub_fair_price", 0.20)
        self.dec_fair_price = config.get("dec_fair_price", 0.45)
        self.edge_threshold = config.get("edge_threshold", 0.10)  # 10% edge needed
        self.position_size = config.get("position_size", 4.0)
        
    def analyze(self, market_data: Dict) -> Optional[Signal]:
        """Look for mispriced MOV markets."""
        market_id = market_data.get("market_id", "")
        yes_price = market_data.get("yes_price", 0.5)
        
        # Only trade MOV markets
        if "KXUFCMOV" not in market_id:
            return None
        
        # Determine type and fair price
        ticker_upper = market_id.upper()
        
        if "KOTKODQ" in ticker_upper or "KO" in ticker_upper:
            fair_price = self.ko_fair_price
            method = "ko"
        elif "SUB" in ticker_upper:
            fair_price = self.sub_fair_price
            method = "submission"
        elif "DEC" in ticker_upper:
            fair_price = self.dec_fair_price
            method = "decision"
        else:
            return None
        
        # Check for edge
        edge = fair_price - yes_price
        
        if edge >= self.edge_threshold and yes_price > 0.03:
            return Signal(
                market_id=market_id,
                side="yes",
                confidence=fair_price,
                size=self.position_size,
                strategy_name=self.name,
                timestamp=datetime.now(),
                metadata={
                    "reason": f"{method}_value",
                    "price": yes_price,
                    "fair_price": fair_price,
                    "edge": edge
                }
            )
        
        return None
    
    def should_exit(self, position: Dict, market_data: Dict) -> bool:
        """Exit when edge disappears or near resolution."""
        entry_price = position.get("entry_price", 0.5)
        current_price = market_data.get("yes_price", 0.5)
        fair_price = position.get("metadata", {}).get("fair_price", 0.5)
        
        # Exit if price exceeds fair value (edge gone)
        if current_price >= fair_price:
            return True
        
        # Stop loss
        if current_price < entry_price * 0.5:
            return True
        
        # Exit close to event
        if market_data.get("close_in_hours", 24) < 0.25:
            return True
        
        return False
