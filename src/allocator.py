"""
Capital Allocator - Ranks strategies and allocates capital dynamically.

Tracks performance of all strategies and allocates more capital
to winners while reducing exposure to underperformers.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import structlog

from .config import Config, AllocatorConfig
from .performance import PerformanceTracker, StrategyMetrics
from .paper_trader import PaperTrader

log = structlog.get_logger()


@dataclass
class AllocationResult:
    """Result of capital allocation calculation."""
    strategy_name: str
    current_capital: float
    new_allocation: float
    allocation_change: float
    rank: int
    score: float
    reason: str


class CapitalAllocator:
    """
    Dynamically allocates capital to strategies based on performance.
    
    Strategies are ranked by a composite score of:
    - Risk-adjusted returns (Sharpe ratio)
    - Win rate
    - Profit factor
    - Max drawdown (negative factor)
    
    Capital is reallocated periodically to favor winners.
    """
    
    def __init__(
        self,
        config: AllocatorConfig,
        performance_tracker: PerformanceTracker,
        paper_trader: PaperTrader,
        total_capital: float,
    ):
        self.config = config
        self.performance_tracker = performance_tracker
        self.paper_trader = paper_trader
        self.total_capital = total_capital
        
        self._last_rebalance: Optional[datetime] = None
        self._allocation_history: List[Dict] = []
    
    def calculate_strategy_score(self, metrics: StrategyMetrics) -> float:
        """
        Calculate composite score for a strategy.
        
        Higher score = better performance = more capital allocation.
        """
        if metrics.total_trades < 3:
            return 0.0  # Need minimum history
        
        # Components (normalized to roughly 0-1 range)
        sharpe_score = max(0, min(1, metrics.sharpe_ratio / 2))  # 0-2 Sharpe -> 0-1
        win_rate_score = metrics.win_rate / 100  # 0-100% -> 0-1
        profit_factor_score = max(0, min(1, (metrics.profit_factor - 1) / 2))  # 1-3 -> 0-1
        
        # Drawdown penalty (higher drawdown = lower score)
        drawdown_penalty = min(1, metrics.max_drawdown / 20)  # 0-20% -> 0-1 penalty
        
        # Weighted composite
        performance_score = (
            0.4 * sharpe_score +
            0.3 * win_rate_score +
            0.3 * profit_factor_score
        )
        
        # Apply weights from config
        score = (
            self.config.performance_weight * performance_score -
            self.config.risk_weight * drawdown_penalty
        )
        
        return max(0, score)
    
    def rank_strategies(self) -> List[Tuple[str, float, StrategyMetrics]]:
        """
        Rank all strategies by composite score.
        
        Returns:
            List of (strategy_name, score, metrics) tuples, sorted by score desc
        """
        rankings = []
        
        for name, portfolio in self.paper_trader.portfolios.items():
            # Calculate fresh metrics
            metrics = self.performance_tracker.calculate_metrics(portfolio)
            score = self.calculate_strategy_score(metrics)
            rankings.append((name, score, metrics))
        
        # Sort by score descending
        rankings.sort(key=lambda x: x[1], reverse=True)
        
        return rankings
    
    def calculate_allocations(self) -> List[AllocationResult]:
        """
        Calculate optimal capital allocations for all strategies.
        
        Uses a proportional allocation based on scores, with
        minimum allocation for all active strategies.
        """
        rankings = self.rank_strategies()
        
        if not rankings:
            return []
        
        results = []
        
        # Filter strategies meeting minimum criteria
        qualified = [
            (name, score, metrics) for name, score, metrics in rankings
            if score > 0 or metrics.total_trades < self.performance_tracker.config.min_trades_for_ranking
        ]
        
        if not qualified:
            # No qualified strategies - equal allocation
            per_strategy = self.total_capital / len(rankings)
            for rank, (name, score, metrics) in enumerate(rankings, 1):
                portfolio = self.paper_trader.get_portfolio(name)
                results.append(AllocationResult(
                    strategy_name=name,
                    current_capital=portfolio.current_capital if portfolio else 0,
                    new_allocation=per_strategy,
                    allocation_change=per_strategy - (portfolio.current_capital if portfolio else 0),
                    rank=rank,
                    score=score,
                    reason="equal_allocation_no_qualified",
                ))
            return results
        
        # Calculate total score for proportional allocation
        total_score = sum(score for _, score, _ in qualified)
        
        # Minimum allocation (10% of equal share)
        min_allocation = (self.total_capital / len(qualified)) * 0.1
        
        for rank, (name, score, metrics) in enumerate(qualified, 1):
            portfolio = self.paper_trader.get_portfolio(name)
            current = portfolio.current_capital if portfolio else 0
            
            if total_score > 0:
                # Proportional allocation based on score
                base_allocation = (score / total_score) * self.total_capital
            else:
                # Equal allocation if no scores
                base_allocation = self.total_capital / len(qualified)
            
            # Apply minimum and maximum constraints
            new_allocation = max(min_allocation, base_allocation)
            
            # Don't exceed what's available
            new_allocation = min(new_allocation, self.total_capital * 0.5)
            
            # Determine reason
            if score >= 0.5:
                reason = "high_performer"
            elif score >= 0.25:
                reason = "moderate_performer"
            elif metrics.total_trades < 5:
                reason = "insufficient_history"
            else:
                reason = "low_performer"
            
            results.append(AllocationResult(
                strategy_name=name,
                current_capital=current,
                new_allocation=new_allocation,
                allocation_change=new_allocation - current,
                rank=rank,
                score=score,
                reason=reason,
            ))
        
        # Normalize to ensure total doesn't exceed available capital
        total_allocated = sum(r.new_allocation for r in results)
        if total_allocated > self.total_capital:
            scale = self.total_capital / total_allocated
            for r in results:
                r.new_allocation *= scale
                r.allocation_change = r.new_allocation - r.current_capital
        
        return results
    
    def should_rebalance(self) -> bool:
        """Check if it's time to rebalance."""
        if self._last_rebalance is None:
            return True
        
        elapsed = datetime.now(timezone.utc) - self._last_rebalance
        interval = timedelta(hours=self.config.rebalance_interval_hours)
        
        return elapsed >= interval
    
    def rebalance(self) -> List[AllocationResult]:
        """
        Execute rebalancing - update strategy capital allocations.
        
        Returns:
            List of allocation changes made
        """
        if not self.should_rebalance():
            log.debug("Rebalance not due yet")
            return []
        
        allocations = self.calculate_allocations()
        
        for alloc in allocations:
            portfolio = self.paper_trader.get_portfolio(alloc.strategy_name)
            if portfolio:
                # Update portfolio capital (paper trading - just update the number)
                old_capital = portfolio.current_capital
                portfolio.current_capital = alloc.new_allocation
                portfolio.initial_capital = alloc.new_allocation  # Reset baseline
                
                log.info(
                    "Capital reallocated",
                    strategy=alloc.strategy_name,
                    old=old_capital,
                    new=alloc.new_allocation,
                    change=alloc.allocation_change,
                    rank=alloc.rank,
                    score=alloc.score,
                    reason=alloc.reason,
                )
        
        self._last_rebalance = datetime.now(timezone.utc)
        self._allocation_history.append({
            "timestamp": self._last_rebalance.isoformat(),
            "allocations": [a.__dict__ for a in allocations],
        })
        
        return allocations
    
    def get_allocation_summary(self) -> Dict:
        """Get current allocation summary."""
        allocations = self.calculate_allocations()
        
        return {
            "total_capital": self.total_capital,
            "last_rebalance": self._last_rebalance.isoformat() if self._last_rebalance else None,
            "strategies": [
                {
                    "name": a.strategy_name,
                    "allocation": a.new_allocation,
                    "allocation_pct": (a.new_allocation / self.total_capital) * 100,
                    "rank": a.rank,
                    "score": a.score,
                    "reason": a.reason,
                }
                for a in allocations
            ]
        }
