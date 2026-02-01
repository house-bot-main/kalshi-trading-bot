"""
Configuration management for Kalshi trading bot.

Loads from config.yaml and environment variables.
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import yaml
from dotenv import load_dotenv


@dataclass
class RiskConfig:
    """Risk management configuration."""
    max_total_capital: float = 200.0  # $200 total
    max_position_size: float = 10.0   # $10 max per trade
    max_daily_loss: float = 20.0      # Stop if down $20/day
    max_concurrent_positions: int = 10
    max_exposure_pct: float = 0.20    # Max 20% capital at risk


@dataclass
class StrategyConfig:
    """Per-strategy configuration."""
    name: str
    enabled: bool = True
    initial_capital: float = 50.0     # Virtual capital allocation
    max_position_size: float = 10.0
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScannerConfig:
    """Market scanner configuration."""
    poll_interval_seconds: float = 30.0
    min_volume: int = 100             # Min daily volume
    min_liquidity: float = 50.0       # Min $ in order book
    market_status: str = "open"
    max_markets: int = 500            # Max markets to scan per cycle


@dataclass
class PerformanceConfig:
    """Performance tracking configuration."""
    db_path: str = "data/performance.db"
    rolling_window_days: int = 30
    min_trades_for_ranking: int = 5


@dataclass
class AllocatorConfig:
    """Capital allocator configuration."""
    rebalance_interval_hours: float = 24.0
    min_sharpe_ratio: float = 0.5
    performance_weight: float = 0.7   # Weight for returns
    risk_weight: float = 0.3          # Weight for risk metrics


@dataclass
class Config:
    """Main configuration container."""
    # API settings
    sandbox: bool = True  # ALWAYS default to sandbox
    api_key_env_var: str = "KALSHI_API_KEY"
    private_key_path: str = "~/.openclaw/.secrets/kalshi_private_key.pem"
    
    # Risk management
    risk: RiskConfig = field(default_factory=RiskConfig)
    
    # Scanner
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    
    # Performance tracking
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    
    # Allocator
    allocator: AllocatorConfig = field(default_factory=AllocatorConfig)
    
    # Strategies
    strategies: Dict[str, StrategyConfig] = field(default_factory=dict)
    
    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = "logs/trading.log"
    
    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        """Load configuration from YAML file."""
        path = Path(config_path)
        
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        
        # Load environment variables
        env_path = Path.home() / ".openclaw" / ".env"
        load_dotenv(env_path)
        
        # Build config
        config = cls()
        
        # API settings
        config.sandbox = data.get("sandbox", True)
        config.api_key_env_var = data.get("api_key_env_var", "KALSHI_API_KEY")
        config.private_key_path = data.get(
            "private_key_path", 
            "~/.openclaw/.secrets/kalshi_private_key.pem"
        )
        
        # CRITICAL SAFETY: Force sandbox in automated mode
        if data.get("force_sandbox", True):
            config.sandbox = True
        
        # Risk config
        risk_data = data.get("risk", {})
        config.risk = RiskConfig(
            max_total_capital=risk_data.get("max_total_capital", 200.0),
            max_position_size=risk_data.get("max_position_size", 10.0),
            max_daily_loss=risk_data.get("max_daily_loss", 20.0),
            max_concurrent_positions=risk_data.get("max_concurrent_positions", 10),
            max_exposure_pct=risk_data.get("max_exposure_pct", 0.20),
        )
        
        # Scanner config
        scanner_data = data.get("scanner", {})
        config.scanner = ScannerConfig(
            poll_interval_seconds=scanner_data.get("poll_interval_seconds", 30.0),
            min_volume=scanner_data.get("min_volume", 100),
            min_liquidity=scanner_data.get("min_liquidity", 50.0),
            market_status=scanner_data.get("market_status", "open"),
        )
        
        # Performance config
        perf_data = data.get("performance", {})
        config.performance = PerformanceConfig(
            db_path=perf_data.get("db_path", "data/performance.db"),
            rolling_window_days=perf_data.get("rolling_window_days", 30),
            min_trades_for_ranking=perf_data.get("min_trades_for_ranking", 5),
        )
        
        # Allocator config
        alloc_data = data.get("allocator", {})
        config.allocator = AllocatorConfig(
            rebalance_interval_hours=alloc_data.get("rebalance_interval_hours", 24.0),
            min_sharpe_ratio=alloc_data.get("min_sharpe_ratio", 0.5),
            performance_weight=alloc_data.get("performance_weight", 0.7),
            risk_weight=alloc_data.get("risk_weight", 0.3),
        )
        
        # Strategy configs
        strategies_data = data.get("strategies", {})
        for name, strat_data in strategies_data.items():
            config.strategies[name] = StrategyConfig(
                name=name,
                enabled=strat_data.get("enabled", True),
                initial_capital=strat_data.get("initial_capital", 50.0),
                max_position_size=strat_data.get("max_position_size", 10.0),
                params=strat_data.get("params", {}),
            )
        
        # Add default strategies if none specified
        if not config.strategies:
            config.strategies = {
                "MeanReversion": StrategyConfig(
                    name="MeanReversion",
                    initial_capital=66.0,
                    params={
                        "extreme_threshold": 0.95,
                        "min_threshold": 0.05,
                        "exit_target": 0.50,
                        "base_position_size": 5.0,
                    }
                ),
                "Momentum": StrategyConfig(
                    name="Momentum",
                    initial_capital=66.0,
                    params={
                        "short_ma": 5,
                        "long_ma": 20,
                        "momentum_threshold": 0.02,
                        "position_size": 5.0,
                    }
                ),
                "MarketMaking": StrategyConfig(
                    name="MarketMaking",
                    initial_capital=66.0,
                    params={
                        "min_spread": 0.05,
                        "position_size": 5.0,
                    }
                ),
            }
        
        # Logging
        config.log_level = data.get("log_level", "INFO")
        config.log_file = data.get("log_file", "logs/trading.log")
        
        return config
    
    def get_api_key(self) -> str:
        """Get API key from environment."""
        key = os.getenv(self.api_key_env_var)
        if not key:
            raise ValueError(f"{self.api_key_env_var} not set in environment")
        return key
    
    def get_private_key_path(self) -> Path:
        """Get expanded private key path."""
        return Path(self.private_key_path).expanduser()


def get_default_config() -> Config:
    """Get default configuration (for testing)."""
    return Config()
