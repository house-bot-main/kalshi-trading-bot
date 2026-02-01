"""
Paper Trading Engine - Simulates trades without real money.

Tracks virtual portfolios per strategy, calculates P&L,
and provides the foundation for performance tracking.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

from .strategies.base import Signal
from .config import Config, RiskConfig

log = structlog.get_logger()


class PositionStatus(Enum):
    """Position lifecycle status."""
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass
class PaperPosition:
    """A simulated position."""
    id: str
    strategy_name: str
    market_id: str
    ticker: str
    side: str  # 'yes' or 'no'
    quantity: int  # Number of contracts
    entry_price: float  # Price per contract (0-1)
    entry_time: datetime
    entry_cost: float  # Total cost in dollars
    
    # Exit info (filled when closed)
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_value: Optional[float] = None
    
    status: PositionStatus = PositionStatus.OPEN
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def pnl(self) -> float:
        """Calculate profit/loss."""
        if self.status == PositionStatus.OPEN:
            return 0.0
        if self.exit_value is not None:
            return self.exit_value - self.entry_cost
        return 0.0
    
    @property
    def pnl_pct(self) -> float:
        """Calculate P&L percentage."""
        if self.entry_cost == 0:
            return 0.0
        return (self.pnl / self.entry_cost) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "strategy_name": self.strategy_name,
            "market_id": self.market_id,
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "entry_cost": self.entry_cost,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_value": self.exit_value,
            "status": self.status.value,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "metadata": self.metadata,
        }


@dataclass
class StrategyPortfolio:
    """Virtual portfolio for a single strategy."""
    strategy_name: str
    initial_capital: float
    current_capital: float
    positions: Dict[str, PaperPosition] = field(default_factory=dict)
    closed_positions: List[PaperPosition] = field(default_factory=list)
    
    @property
    def open_positions(self) -> List[PaperPosition]:
        """Get list of open positions."""
        return [p for p in self.positions.values() if p.status == PositionStatus.OPEN]
    
    @property
    def total_exposure(self) -> float:
        """Total capital in open positions."""
        return sum(p.entry_cost for p in self.open_positions)
    
    @property
    def available_capital(self) -> float:
        """Capital available for new positions."""
        return self.current_capital - self.total_exposure
    
    @property
    def total_pnl(self) -> float:
        """Total realized P&L."""
        return sum(p.pnl for p in self.closed_positions)
    
    @property
    def total_trades(self) -> int:
        """Total number of closed trades."""
        return len(self.closed_positions)
    
    @property
    def winning_trades(self) -> int:
        """Number of profitable trades."""
        return sum(1 for p in self.closed_positions if p.pnl > 0)
    
    @property
    def win_rate(self) -> float:
        """Win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100


class PaperTrader:
    """
    Paper trading engine that simulates trades for all strategies.
    
    Each strategy gets its own virtual portfolio. Trades are executed
    instantly at the signal's indicated price (no slippage simulation).
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.risk_config = config.risk
        
        # Portfolio per strategy
        self.portfolios: Dict[str, StrategyPortfolio] = {}
        
        # Daily tracking
        self._daily_pnl: float = 0.0
        self._daily_start_date: Optional[datetime] = None
        
    def initialize_portfolio(self, strategy_name: str, capital: float):
        """Initialize a portfolio for a strategy."""
        self.portfolios[strategy_name] = StrategyPortfolio(
            strategy_name=strategy_name,
            initial_capital=capital,
            current_capital=capital,
        )
        log.info(
            "Initialized portfolio",
            strategy=strategy_name,
            capital=capital,
        )
    
    def get_portfolio(self, strategy_name: str) -> Optional[StrategyPortfolio]:
        """Get portfolio for a strategy."""
        return self.portfolios.get(strategy_name)
    
    def execute_signal(
        self,
        signal: Signal,
        market_price: float,
    ) -> Optional[PaperPosition]:
        """
        Execute a trading signal in paper mode.
        
        Args:
            signal: Trading signal from strategy
            market_price: Current market YES price
            
        Returns:
            Created position or None if rejected
        """
        portfolio = self.portfolios.get(signal.strategy_name)
        if not portfolio:
            log.warning("No portfolio for strategy", strategy=signal.strategy_name)
            return None
        
        # Check risk limits
        if not self._check_risk_limits(signal, portfolio):
            return None
        
        # Calculate position size
        price = market_price if signal.side == "yes" else (1.0 - market_price)
        quantity = int(signal.size / price) if price > 0 else 0
        
        if quantity <= 0:
            log.debug("Quantity too small", signal=signal)
            return None
        
        entry_cost = quantity * price
        
        # Create position
        position = PaperPosition(
            id=str(uuid.uuid4()),
            strategy_name=signal.strategy_name,
            market_id=signal.market_id,
            ticker=signal.market_id,  # Assuming market_id is ticker
            side=signal.side,
            quantity=quantity,
            entry_price=price,
            entry_time=datetime.now(timezone.utc),
            entry_cost=entry_cost,
            metadata=signal.metadata or {},
        )
        
        portfolio.positions[position.id] = position
        
        log.info(
            "Paper trade opened",
            strategy=signal.strategy_name,
            market=signal.market_id,
            side=signal.side,
            quantity=quantity,
            price=price,
            cost=entry_cost,
        )
        
        return position
    
    def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: str = "signal",
    ) -> Optional[PaperPosition]:
        """
        Close an open position.
        
        Args:
            position_id: Position to close
            exit_price: Exit price (market YES price)
            reason: Why position was closed
            
        Returns:
            Closed position or None
        """
        # Find position across portfolios
        for portfolio in self.portfolios.values():
            if position_id in portfolio.positions:
                position = portfolio.positions[position_id]
                
                # Calculate exit value
                price = exit_price if position.side == "yes" else (1.0 - exit_price)
                exit_value = position.quantity * price
                
                # Update position
                position.exit_price = price
                position.exit_time = datetime.now(timezone.utc)
                position.exit_value = exit_value
                position.status = PositionStatus.CLOSED
                position.metadata["close_reason"] = reason
                
                # Update portfolio
                portfolio.current_capital += position.pnl
                portfolio.closed_positions.append(position)
                del portfolio.positions[position_id]
                
                # Update daily P&L
                self._daily_pnl += position.pnl
                
                log.info(
                    "Paper trade closed",
                    strategy=portfolio.strategy_name,
                    market=position.ticker,
                    pnl=position.pnl,
                    pnl_pct=position.pnl_pct,
                    reason=reason,
                )
                
                return position
        
        log.warning("Position not found", position_id=position_id)
        return None
    
    def check_exits(
        self,
        strategy,
        market_data: Dict[str, Any],
    ) -> List[PaperPosition]:
        """
        Check if any open positions should be closed.
        
        Args:
            strategy: Strategy to check positions for
            market_data: Current market data
            
        Returns:
            List of closed positions
        """
        portfolio = self.portfolios.get(strategy.name)
        if not portfolio:
            return []
        
        closed = []
        
        for position in list(portfolio.open_positions):
            if position.ticker != market_data.get("market_id"):
                continue
            
            # Convert position to dict for strategy
            pos_dict = {
                "entry_price": position.entry_price,
                "side": position.side,
                "metadata": position.metadata,
            }
            
            if strategy.should_exit(pos_dict, market_data):
                result = self.close_position(
                    position.id,
                    market_data.get("yes_price", 0.5),
                    reason="strategy_exit",
                )
                if result:
                    closed.append(result)
        
        return closed
    
    def _check_risk_limits(
        self,
        signal: Signal,
        portfolio: StrategyPortfolio,
    ) -> bool:
        """Check if trade passes risk limits."""
        # Check available capital
        if signal.size > portfolio.available_capital:
            log.debug(
                "Insufficient capital",
                required=signal.size,
                available=portfolio.available_capital,
            )
            return False
        
        # Check max position size
        if signal.size > self.risk_config.max_position_size:
            log.debug(
                "Position too large",
                size=signal.size,
                max=self.risk_config.max_position_size,
            )
            return False
        
        # Check total exposure
        total_exposure = sum(p.total_exposure for p in self.portfolios.values())
        max_exposure = self.risk_config.max_total_capital * self.risk_config.max_exposure_pct
        if total_exposure + signal.size > max_exposure:
            log.debug(
                "Would exceed max exposure",
                current=total_exposure,
                new=signal.size,
                max=max_exposure,
            )
            return False
        
        # Check concurrent positions
        total_positions = sum(
            len(p.open_positions) for p in self.portfolios.values()
        )
        if total_positions >= self.risk_config.max_concurrent_positions:
            log.debug("Max positions reached", current=total_positions)
            return False
        
        # Check daily loss limit
        if self._daily_pnl < -self.risk_config.max_daily_loss:
            log.warning("Daily loss limit reached", loss=self._daily_pnl)
            return False
        
        return True
    
    def reset_daily_stats(self):
        """Reset daily statistics (call at start of day)."""
        self._daily_pnl = 0.0
        self._daily_start_date = datetime.now(timezone.utc).date()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all portfolios."""
        summary = {
            "total_capital": sum(p.current_capital for p in self.portfolios.values()),
            "total_pnl": sum(p.total_pnl for p in self.portfolios.values()),
            "daily_pnl": self._daily_pnl,
            "strategies": {},
        }
        
        for name, portfolio in self.portfolios.items():
            summary["strategies"][name] = {
                "capital": portfolio.current_capital,
                "available": portfolio.available_capital,
                "exposure": portfolio.total_exposure,
                "open_positions": len(portfolio.open_positions),
                "total_trades": portfolio.total_trades,
                "win_rate": portfolio.win_rate,
                "total_pnl": portfolio.total_pnl,
            }
        
        return summary
    
    def get_all_positions(self, status: str = "open") -> List[PaperPosition]:
        """Get all positions across portfolios."""
        positions = []
        for portfolio in self.portfolios.values():
            if status == "open":
                positions.extend(portfolio.open_positions)
            elif status == "closed":
                positions.extend(portfolio.closed_positions)
            else:
                positions.extend(portfolio.positions.values())
                positions.extend(portfolio.closed_positions)
        return positions
