"""
Orchestrator - Main loop that ties everything together.

Responsibilities:
- Initialize all components
- Run the main trading loop
- Coordinate data flow: scanner -> strategies -> paper trader
- Handle exits and performance tracking
- Periodic rebalancing
"""
import asyncio
import signal
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path
import structlog

from .api.kalshi_client import KalshiClient, create_client_from_env
from .config import Config
from .scanner import MarketScanner, MarketData
from .paper_trader import PaperTrader
from .performance import PerformanceTracker
from .allocator import CapitalAllocator
from .strategies.base import BaseStrategy, Signal
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.momentum import MomentumStrategy
from .strategies.market_making import MarketMakingStrategy

log = structlog.get_logger()


class Orchestrator:
    """
    Main orchestrator that runs the trading bot.
    
    Flow:
    1. Initialize API client, strategies, paper trader
    2. Scanner polls markets
    3. Each strategy analyzes market data
    4. Signals are executed via paper trader
    5. Performance is tracked
    6. Capital is reallocated periodically
    """
    
    def __init__(self, config: Config):
        self.config = config
        
        # Will be initialized in start()
        self._client: Optional[KalshiClient] = None
        self._scanner: Optional[MarketScanner] = None
        self._paper_trader: Optional[PaperTrader] = None
        self._performance_tracker: Optional[PerformanceTracker] = None
        self._allocator: Optional[CapitalAllocator] = None
        
        # Strategies
        self._strategies: List[BaseStrategy] = []
        
        # Control
        self._running = False
        self._shutdown_event = asyncio.Event()
    
    def _create_strategies(self) -> List[BaseStrategy]:
        """Create strategy instances from config."""
        strategies = []
        
        for name, strat_config in self.config.strategies.items():
            if not strat_config.enabled:
                continue
            
            params = strat_config.params.copy()
            params["max_position_size"] = strat_config.max_position_size
            
            if name == "MeanReversion":
                strategy = MeanReversionStrategy(params)
            elif name == "Momentum":
                strategy = MomentumStrategy(params)
            elif name == "MarketMaking":
                strategy = MarketMakingStrategy(params)
            else:
                log.warning("Unknown strategy", name=name)
                continue
            
            # Force paper mode
            strategy.enable_paper_mode()
            strategies.append(strategy)
            log.info("Strategy initialized", name=name, params=params)
        
        return strategies
    
    async def initialize(self) -> bool:
        """
        Initialize all components.
        
        Returns:
            True if initialization successful
        """
        log.info("Initializing orchestrator...")
        
        # Ensure data directory exists
        Path("data").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        
        # Create API client
        try:
            self._client = create_client_from_env(sandbox=self.config.sandbox)
            log.info("API client created", sandbox=self.config.sandbox)
        except Exception as e:
            log.error("Failed to create API client", error=str(e))
            return False
        
        # Test API connection
        try:
            balance = await self._client.get_balance()
            log.info("API connection verified", balance=balance)
        except Exception as e:
            log.error("API connection failed", error=str(e))
            return False
        
        # Create strategies
        self._strategies = self._create_strategies()
        if not self._strategies:
            log.error("No strategies enabled")
            return False
        
        # Create scanner
        self._scanner = MarketScanner(
            client=self._client,
            config=self.config.scanner,
            strategies=self._strategies,
        )
        
        # Create paper trader
        self._paper_trader = PaperTrader(self.config)
        
        # Initialize portfolios
        for name, strat_config in self.config.strategies.items():
            if strat_config.enabled:
                self._paper_trader.initialize_portfolio(
                    strategy_name=name,
                    capital=strat_config.initial_capital,
                )
        
        # Create performance tracker
        self._performance_tracker = PerformanceTracker(self.config.performance)
        
        # Create capital allocator
        self._allocator = CapitalAllocator(
            config=self.config.allocator,
            performance_tracker=self._performance_tracker,
            paper_trader=self._paper_trader,
            total_capital=self.config.risk.max_total_capital,
        )
        
        log.info("Orchestrator initialized successfully")
        return True
    
    async def run(self):
        """
        Run the main trading loop.
        
        This is the main entry point after initialization.
        """
        self._running = True
        print("[DEBUG] run() started, _running=True", flush=True)
        log.info("Starting trading loop...")
        
        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
        
        print("[DEBUG] Entering scanner loop...", flush=True)
        try:
            async for signals in self._scanner.run_continuous():
                print(f"[DEBUG] Got {len(signals)} signals", flush=True)
                if not self._running:
                    print("[DEBUG] _running is False, breaking", flush=True)
                    break
                
                await self._process_cycle(signals)
                print("[DEBUG] Cycle processed, waiting for next...", flush=True)
                
        except asyncio.CancelledError:
            print("[DEBUG] CancelledError", flush=True)
            log.info("Trading loop cancelled")
        except Exception as e:
            print(f"[DEBUG] Exception: {e}", flush=True)
            log.error("Trading loop error", error=str(e), exc_info=True)
        finally:
            print("[DEBUG] Cleaning up...", flush=True)
            await self.cleanup()
    
    async def _process_cycle(self, signals: List[Signal]):
        """
        Process one cycle of the trading loop.
        
        Args:
            signals: Signals generated from current scan
        """
        now = datetime.now(timezone.utc)
        
        # 1. Process signals - execute paper trades
        for signal in signals:
            if signal.side == "both":
                # Market making - skip for now (complex)
                continue
            
            # Get current market price
            market = self._scanner.get_market(signal.market_id)
            if not market:
                continue
            
            # Execute paper trade
            position = self._paper_trader.execute_signal(signal, market.yes_price)
            if position:
                log.info(
                    "Position opened",
                    strategy=signal.strategy_name,
                    market=signal.market_id,
                    side=signal.side,
                )
        
        # 2. Check exits for existing positions
        for market in self._scanner.get_all_markets():
            market_dict = market.to_dict()
            
            for strategy in self._strategies:
                closed = self._paper_trader.check_exits(strategy, market_dict)
                
                # Record closed trades
                for position in closed:
                    self._performance_tracker.record_trade(position)
                    
                    # Record capital snapshot
                    portfolio = self._paper_trader.get_portfolio(strategy.name)
                    if portfolio:
                        self._performance_tracker.record_capital_snapshot(
                            strategy.name,
                            portfolio.current_capital,
                        )
        
        # 3. Periodic rebalancing
        if self._allocator.should_rebalance():
            allocations = self._allocator.rebalance()
            if allocations:
                log.info("Capital reallocated", count=len(allocations))
        
        # 4. Log status periodically
        if hasattr(self, "_last_status") and (now - self._last_status).seconds < 300:
            return
        self._last_status = now
        
        summary = self._paper_trader.get_summary()
        log.info(
            "Trading status",
            total_capital=summary["total_capital"],
            total_pnl=summary["total_pnl"],
            daily_pnl=summary["daily_pnl"],
        )
    
    async def run_once(self) -> Dict:
        """
        Run a single scan cycle (for testing).
        
        Returns:
            Summary of what happened
        """
        if not self._scanner:
            raise RuntimeError("Orchestrator not initialized")
        
        markets = await self._scanner.scan_once()
        signals = await self._scanner.generate_signals(markets)
        await self._process_cycle(signals)
        
        return {
            "markets_scanned": len(markets),
            "signals_generated": len(signals),
            "summary": self._paper_trader.get_summary(),
        }
    
    async def shutdown(self):
        """Signal graceful shutdown."""
        log.info("Shutdown requested...")
        self._running = False
        self._shutdown_event.set()
        if self._scanner:
            self._scanner.stop()
    
    async def cleanup(self):
        """Clean up resources."""
        log.info("Cleaning up...")
        
        # Save final metrics
        if self._performance_tracker and self._paper_trader:
            for name, portfolio in self._paper_trader.portfolios.items():
                metrics = self._performance_tracker.calculate_metrics(portfolio)
                self._performance_tracker.save_daily_metrics(name, metrics)
        
        # Close API client
        if self._client:
            await self._client.close()
        
        log.info("Cleanup complete")
    
    def get_status(self) -> Dict:
        """Get current status of the bot."""
        status = {
            "running": self._running,
            "sandbox": self.config.sandbox,
            "strategies": [s.name for s in self._strategies],
        }
        
        if self._paper_trader:
            status["portfolio"] = self._paper_trader.get_summary()
        
        if self._performance_tracker:
            status["metrics"] = {
                name: m.to_dict()
                for name, m in self._performance_tracker.get_all_metrics().items()
            }
        
        if self._allocator:
            status["allocations"] = self._allocator.get_allocation_summary()
        
        return status


async def run_bot(config_path: str = "config.yaml", sandbox: bool = True):
    """
    Main entry point to run the bot.
    
    Args:
        config_path: Path to config file
        sandbox: Force sandbox mode (default True, RECOMMENDED)
    """
    print(f"[DEBUG] run_bot called: config_path={config_path}, sandbox={sandbox}", flush=True)
    
    # Load config
    print("[DEBUG] Loading config...", flush=True)
    config = Config.load(config_path)
    print(f"[DEBUG] Config loaded, sandbox={config.sandbox}", flush=True)
    
    # CRITICAL: Override to sandbox if requested
    if sandbox:
        config.sandbox = True
        print("[DEBUG] Forcing sandbox mode", flush=True)
    
    print(f"[DEBUG] Creating orchestrator...", flush=True)
    
    # Create and run orchestrator
    orchestrator = Orchestrator(config)
    print("[DEBUG] Orchestrator created, initializing...", flush=True)
    
    if await orchestrator.initialize():
        print("[DEBUG] Orchestrator initialized, starting run loop...", flush=True)
        await orchestrator.run()
    else:
        print("[DEBUG] Failed to initialize orchestrator!", flush=True)
        log.error("Failed to initialize orchestrator")
