"""
Performance Tracker - Calculates and stores strategy metrics.

Tracks:
- Per-strategy P&L, returns, Sharpe ratio, max drawdown, win rate
- Rolling window calculations
- SQLite storage for historical analysis
"""
import sqlite3
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
import structlog

from .paper_trader import PaperPosition, StrategyPortfolio
from .config import PerformanceConfig

log = structlog.get_logger()


@dataclass
class StrategyMetrics:
    """Performance metrics for a single strategy."""
    strategy_name: str
    
    # Returns
    total_return: float = 0.0          # Total $ P&L
    total_return_pct: float = 0.0      # Total % return
    
    # Risk-adjusted
    sharpe_ratio: float = 0.0          # Annualized Sharpe
    sortino_ratio: float = 0.0         # Downside risk adjusted
    max_drawdown: float = 0.0          # Max peak-to-trough
    
    # Win/loss
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # Profit metrics
    average_win: float = 0.0
    average_loss: float = 0.0
    profit_factor: float = 0.0         # Gross profit / gross loss
    expectancy: float = 0.0            # Expected $ per trade
    
    # Time metrics
    average_hold_time_hours: float = 0.0
    
    # Capital
    current_capital: float = 0.0
    peak_capital: float = 0.0
    
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "total_return": self.total_return,
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown": self.max_drawdown,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "average_hold_time_hours": self.average_hold_time_hours,
            "current_capital": self.current_capital,
            "peak_capital": self.peak_capital,
            "last_updated": self.last_updated.isoformat(),
        }


class PerformanceTracker:
    """
    Tracks and calculates performance metrics for all strategies.
    
    Uses SQLite for persistent storage of trade history and metrics.
    """
    
    def __init__(self, config: PerformanceConfig):
        self.config = config
        self._db_path = Path(config.db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # In-memory metrics cache
        self._metrics: Dict[str, StrategyMetrics] = {}
        
        # Capital history for drawdown calculation
        self._capital_history: Dict[str, List[float]] = {}
        
        # Initialize database
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database schema."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        
        # Trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                market_id TEXT NOT NULL,
                ticker TEXT,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                entry_cost REAL NOT NULL,
                exit_price REAL,
                exit_time TEXT,
                exit_value REAL,
                pnl REAL,
                status TEXT NOT NULL,
                metadata TEXT
            )
        """)
        
        # Daily metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                capital REAL NOT NULL,
                pnl REAL NOT NULL,
                trades INTEGER NOT NULL,
                win_rate REAL,
                sharpe_ratio REAL,
                UNIQUE(date, strategy_name)
            )
        """)
        
        # Capital snapshots for drawdown
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS capital_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                capital REAL NOT NULL
            )
        """)
        
        conn.commit()
        conn.close()
        
        log.info("Performance DB initialized", path=str(self._db_path))
    
    def record_trade(self, position: PaperPosition):
        """Record a closed trade to the database."""
        import json
        
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trades 
            (id, strategy_name, market_id, ticker, side, quantity,
             entry_price, entry_time, entry_cost, exit_price, exit_time,
             exit_value, pnl, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.id,
            position.strategy_name,
            position.market_id,
            position.ticker,
            position.side,
            position.quantity,
            position.entry_price,
            position.entry_time.isoformat(),
            position.entry_cost,
            position.exit_price,
            position.exit_time.isoformat() if position.exit_time else None,
            position.exit_value,
            position.pnl,
            position.status.value,
            json.dumps(position.metadata),
        ))
        
        conn.commit()
        conn.close()
        
        log.debug("Trade recorded", trade_id=position.id, pnl=position.pnl)
    
    def record_capital_snapshot(self, strategy_name: str, capital: float):
        """Record capital snapshot for drawdown calculation."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO capital_history (timestamp, strategy_name, capital)
            VALUES (?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            strategy_name,
            capital,
        ))
        
        conn.commit()
        conn.close()
        
        # Update in-memory history
        if strategy_name not in self._capital_history:
            self._capital_history[strategy_name] = []
        self._capital_history[strategy_name].append(capital)
    
    def calculate_metrics(self, portfolio: StrategyPortfolio) -> StrategyMetrics:
        """
        Calculate all performance metrics for a strategy.
        
        Args:
            portfolio: Strategy portfolio with trade history
            
        Returns:
            Calculated metrics
        """
        strategy_name = portfolio.strategy_name
        trades = portfolio.closed_positions
        
        metrics = StrategyMetrics(strategy_name=strategy_name)
        metrics.current_capital = portfolio.current_capital
        
        if not trades:
            return metrics
        
        # Basic stats
        metrics.total_trades = len(trades)
        metrics.total_return = sum(t.pnl for t in trades)
        metrics.total_return_pct = (metrics.total_return / portfolio.initial_capital) * 100
        
        # Win/loss breakdown
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        
        metrics.winning_trades = len(wins)
        metrics.losing_trades = len(losses)
        metrics.win_rate = (metrics.winning_trades / metrics.total_trades) * 100
        
        # Average win/loss
        if wins:
            metrics.average_win = sum(t.pnl for t in wins) / len(wins)
        if losses:
            metrics.average_loss = sum(t.pnl for t in losses) / len(losses)
        
        # Profit factor
        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Expectancy
        metrics.expectancy = metrics.total_return / metrics.total_trades
        
        # Hold time
        hold_times = []
        for t in trades:
            if t.exit_time:
                hold_time = (t.exit_time - t.entry_time).total_seconds() / 3600
                hold_times.append(hold_time)
        if hold_times:
            metrics.average_hold_time_hours = sum(hold_times) / len(hold_times)
        
        # Sharpe ratio (simplified - daily returns)
        metrics.sharpe_ratio = self._calculate_sharpe(trades, portfolio.initial_capital)
        
        # Sortino ratio
        metrics.sortino_ratio = self._calculate_sortino(trades, portfolio.initial_capital)
        
        # Max drawdown
        metrics.max_drawdown = self._calculate_max_drawdown(strategy_name, portfolio)
        metrics.peak_capital = max(self._capital_history.get(strategy_name, [portfolio.initial_capital]))
        
        metrics.last_updated = datetime.now(timezone.utc)
        
        # Cache
        self._metrics[strategy_name] = metrics
        
        return metrics
    
    def _calculate_sharpe(
        self,
        trades: List[PaperPosition],
        initial_capital: float,
        risk_free_rate: float = 0.05,
    ) -> float:
        """Calculate annualized Sharpe ratio."""
        if len(trades) < 2:
            return 0.0
        
        # Calculate returns per trade
        returns = [t.pnl / initial_capital for t in trades]
        
        # Mean and std
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_return = math.sqrt(variance) if variance > 0 else 0
        
        if std_return == 0:
            return 0.0
        
        # Annualize (assume ~250 trading days, ~10 trades/day)
        trades_per_year = 2500
        annualized_return = mean_return * trades_per_year
        annualized_std = std_return * math.sqrt(trades_per_year)
        
        return (annualized_return - risk_free_rate) / annualized_std
    
    def _calculate_sortino(
        self,
        trades: List[PaperPosition],
        initial_capital: float,
        risk_free_rate: float = 0.05,
    ) -> float:
        """Calculate Sortino ratio (penalizes downside only)."""
        if len(trades) < 2:
            return 0.0
        
        returns = [t.pnl / initial_capital for t in trades]
        mean_return = sum(returns) / len(returns)
        
        # Downside deviation (only negative returns)
        negative_returns = [r for r in returns if r < 0]
        if not negative_returns:
            return float('inf')  # No downside
        
        downside_variance = sum(r ** 2 for r in negative_returns) / len(returns)
        downside_std = math.sqrt(downside_variance)
        
        if downside_std == 0:
            return 0.0
        
        # Annualize
        trades_per_year = 2500
        annualized_return = mean_return * trades_per_year
        annualized_downside = downside_std * math.sqrt(trades_per_year)
        
        return (annualized_return - risk_free_rate) / annualized_downside
    
    def _calculate_max_drawdown(
        self,
        strategy_name: str,
        portfolio: StrategyPortfolio,
    ) -> float:
        """Calculate maximum drawdown percentage."""
        history = self._capital_history.get(strategy_name, [])
        
        if not history:
            # Build from trade history
            capital = portfolio.initial_capital
            history = [capital]
            for trade in sorted(portfolio.closed_positions, key=lambda t: t.exit_time or t.entry_time):
                capital += trade.pnl
                history.append(capital)
        
        if len(history) < 2:
            return 0.0
        
        peak = history[0]
        max_dd = 0.0
        
        for capital in history:
            if capital > peak:
                peak = capital
            drawdown = (peak - capital) / peak if peak > 0 else 0
            max_dd = max(max_dd, drawdown)
        
        return max_dd * 100  # Return as percentage
    
    def get_metrics(self, strategy_name: str) -> Optional[StrategyMetrics]:
        """Get cached metrics for a strategy."""
        return self._metrics.get(strategy_name)
    
    def get_all_metrics(self) -> Dict[str, StrategyMetrics]:
        """Get all cached metrics."""
        return self._metrics.copy()
    
    def get_rolling_metrics(
        self,
        strategy_name: str,
        days: Optional[int] = None,
    ) -> StrategyMetrics:
        """
        Calculate metrics for a rolling window.
        
        Args:
            strategy_name: Strategy to analyze
            days: Rolling window in days (None = use config default)
        """
        if days is None:
            days = self.config.rolling_window_days
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Query trades from database
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trades
            WHERE strategy_name = ? AND exit_time >= ?
            ORDER BY exit_time
        """, (strategy_name, cutoff.isoformat()))
        
        rows = cursor.fetchall()
        conn.close()
        
        # TODO: Convert rows to PaperPositions and calculate metrics
        # For now, return cached metrics
        return self._metrics.get(strategy_name, StrategyMetrics(strategy_name=strategy_name))
    
    def save_daily_metrics(self, strategy_name: str, metrics: StrategyMetrics):
        """Save daily metrics snapshot."""
        today = datetime.now(timezone.utc).date().isoformat()
        
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO daily_metrics
            (date, strategy_name, capital, pnl, trades, win_rate, sharpe_ratio)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            strategy_name,
            metrics.current_capital,
            metrics.total_return,
            metrics.total_trades,
            metrics.win_rate,
            metrics.sharpe_ratio,
        ))
        
        conn.commit()
        conn.close()
    
    def get_leaderboard(self) -> List[StrategyMetrics]:
        """Get strategies ranked by performance."""
        metrics_list = list(self._metrics.values())
        
        # Filter by minimum trades
        qualified = [
            m for m in metrics_list 
            if m.total_trades >= self.config.min_trades_for_ranking
        ]
        
        # Sort by Sharpe ratio (risk-adjusted return)
        return sorted(qualified, key=lambda m: m.sharpe_ratio, reverse=True)
