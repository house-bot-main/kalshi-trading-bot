"""
Market Scanner - Polls markets and feeds data to strategies.

Responsibilities:
- Poll active markets at regular intervals
- Fetch order book data for promising markets
- Transform API data into strategy-friendly format
- Identify opportunities across all markets
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, AsyncGenerator
from dataclasses import dataclass, field
import structlog

from .api.kalshi_client import KalshiClient
from .config import Config, ScannerConfig
from .strategies.base import BaseStrategy, Signal

log = structlog.get_logger()


@dataclass
class MarketData:
    """Normalized market data for strategies."""
    market_id: str
    ticker: str
    title: str
    yes_price: float
    no_price: float
    volume: int
    volume_24h: int
    open_interest: int
    status: str
    close_time: Optional[datetime] = None
    order_book: Dict[str, Any] = field(default_factory=dict)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def close_in_hours(self) -> float:
        """Hours until market closes."""
        if not self.close_time:
            return 9999.0
        delta = self.close_time - datetime.now(timezone.utc)
        return max(0, delta.total_seconds() / 3600)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for strategy consumption."""
        return {
            "market_id": self.market_id,
            "ticker": self.ticker,
            "title": self.title,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "volume": self.volume,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "status": self.status,
            "close_in_hours": self.close_in_hours,
            "order_book": self.order_book,
            "last_updated": self.last_updated.isoformat(),
        }


class MarketScanner:
    """
    Scans Kalshi markets and identifies trading opportunities.
    
    This scanner:
    1. Polls all active markets periodically
    2. Filters based on liquidity/volume requirements
    3. Fetches order book data for qualifying markets
    4. Yields opportunities to the orchestrator
    """
    
    def __init__(
        self,
        client: KalshiClient,
        config: ScannerConfig,
        strategies: List[BaseStrategy],
    ):
        self.client = client
        self.config = config
        self.strategies = strategies
        
        # Cache of market data
        self._market_cache: Dict[str, MarketData] = {}
        self._last_scan: Optional[datetime] = None
        
        # Control flags
        self._running = False
        
    async def scan_once(self) -> List[MarketData]:
        """
        Perform a single scan of all markets.
        
        Returns list of markets meeting filter criteria.
        """
        qualifying_markets = []
        cursor = None
        total_scanned = 0
        max_markets = getattr(self.config, 'max_markets', 500)
        
        log.info("Starting market scan", max_markets=max_markets)
        
        while True:
            # Fetch markets page
            response = await self.client.get_markets(
                status=self.config.market_status,
                limit=100,
                cursor=cursor,
            )
            
            markets = response.get("markets", [])
            if not markets:
                break
            
            for market in markets:
                total_scanned += 1
                
                # Respect max_markets limit
                if total_scanned > max_markets:
                    log.info("Reached max_markets limit", limit=max_markets)
                    break
                
                market_data = self._parse_market(market)
                
                # Apply filters
                if self._passes_filters(market_data):
                    # Fetch order book for qualifying markets
                    try:
                        orderbook = await self.client.get_orderbook(market_data.ticker)
                        market_data.order_book = self._parse_orderbook(orderbook)
                    except Exception as e:
                        log.warning("Failed to fetch orderbook", ticker=market_data.ticker, error=str(e))
                    
                    qualifying_markets.append(market_data)
                    self._market_cache[market_data.ticker] = market_data
            
            # Check if we hit the limit
            if total_scanned >= max_markets:
                break
            
            # Pagination
            cursor = response.get("cursor")
            if not cursor:
                break
        
        self._last_scan = datetime.now(timezone.utc)
        log.info("Scan complete", markets_found=len(qualifying_markets))
        
        return qualifying_markets
    
    def _parse_market(self, raw: Dict) -> MarketData:
        """Parse raw API market data into MarketData."""
        # Kalshi prices are in cents (1-99)
        yes_price = raw.get("yes_bid", 50) / 100.0
        no_price = 1.0 - yes_price
        
        # Handle close time
        close_time = None
        if raw.get("close_time"):
            try:
                close_time = datetime.fromisoformat(
                    raw["close_time"].replace("Z", "+00:00")
                )
            except:
                pass
        
        return MarketData(
            market_id=raw.get("id", raw.get("ticker")),
            ticker=raw.get("ticker", ""),
            title=raw.get("title", ""),
            yes_price=yes_price,
            no_price=no_price,
            volume=raw.get("volume", 0),
            volume_24h=raw.get("volume_24h", 0),
            open_interest=raw.get("open_interest", 0),
            status=raw.get("status", "unknown"),
            close_time=close_time,
        )
    
    def _parse_orderbook(self, raw: Dict) -> Dict[str, Any]:
        """Parse raw orderbook into strategy-friendly format."""
        orderbook = raw.get("orderbook", {})
        
        # Yes bids/asks
        yes_bids = orderbook.get("yes", [])
        no_bids = orderbook.get("no", [])
        
        # Find best bid/ask (prices are in cents)
        best_yes_bid = max([b[0] for b in yes_bids], default=0) / 100.0 if yes_bids else 0
        best_yes_ask = min([a[0] for a in no_bids], default=100) / 100.0 if no_bids else 1.0
        
        # The NO ask is effectively 1 - YES bid
        # Calculate effective best bid/ask for YES
        return {
            "best_bid": best_yes_bid,
            "best_ask": 1.0 - best_yes_bid if best_yes_bid > 0 else 1.0,
            "yes_bids": yes_bids,
            "no_bids": no_bids,
            "spread": (1.0 - best_yes_bid) - best_yes_bid if best_yes_bid > 0 else 1.0,
        }
    
    def _passes_filters(self, market: MarketData) -> bool:
        """Check if market passes filter criteria."""
        # Volume filter
        if market.volume_24h < self.config.min_volume:
            return False
        
        # Must be open
        if market.status != "open":
            return False
        
        # Must have some time left
        if market.close_in_hours < 1:
            return False
        
        return True
    
    async def generate_signals(
        self,
        markets: List[MarketData],
    ) -> List[Signal]:
        """
        Run all strategies on market data and collect signals.
        
        Args:
            markets: List of market data to analyze
            
        Returns:
            List of signals from all strategies
        """
        signals = []
        
        for market in markets:
            market_dict = market.to_dict()
            
            for strategy in self.strategies:
                if not strategy.active:
                    continue
                    
                try:
                    signal = strategy.analyze(market_dict)
                    if signal:
                        signals.append(signal)
                        log.info(
                            "Signal generated",
                            strategy=strategy.name,
                            market=market.ticker,
                            side=signal.side,
                            confidence=signal.confidence,
                        )
                except Exception as e:
                    log.error(
                        "Strategy error",
                        strategy=strategy.name,
                        market=market.ticker,
                        error=str(e),
                    )
        
        return signals
    
    async def run_continuous(self) -> AsyncGenerator[List[Signal], None]:
        """
        Run continuous scanning loop.
        
        Yields list of signals on each scan cycle.
        """
        self._running = True
        
        while self._running:
            try:
                # Scan markets
                markets = await self.scan_once()
                
                # Generate signals
                signals = await self.generate_signals(markets)
                
                yield signals
                
                # Wait for next interval
                await asyncio.sleep(self.config.poll_interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Scanner error", error=str(e))
                await asyncio.sleep(5)  # Back off on error
    
    def stop(self):
        """Stop the continuous scan loop."""
        self._running = False
    
    def get_market(self, ticker: str) -> Optional[MarketData]:
        """Get cached market data by ticker."""
        return self._market_cache.get(ticker)
    
    def get_all_markets(self) -> List[MarketData]:
        """Get all cached markets."""
        return list(self._market_cache.values())
