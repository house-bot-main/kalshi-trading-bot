#!/usr/bin/env python3
"""
Minimal API test - uses only standard library + cryptography.

This test verifies the RSA signing and basic API structure work
without requiring all dependencies installed.

Run with: python3 tests/test_api_minimal.py
"""
import os
import sys
import base64
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# Load env vars
def load_env(path):
    """Simple .env loader."""
    env_path = Path(path).expanduser()
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

load_env("~/.openclaw/.env")

# Get credentials
API_KEY = os.getenv("KALSHI_API_KEY")
PRIVATE_KEY_PATH = Path.home() / ".openclaw" / ".secrets" / "kalshi_private_key.pem"

# URLs - Note: Demo and Production require SEPARATE API keys!
# Production API migrated in late 2025
SANDBOX_URL = "https://demo-api.kalshi.co/trade-api/v2"
PRODUCTION_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Set this to test production (if you have production API key)
USE_PRODUCTION = False  # Change to True to test production API


def load_private_key(key_path: Path):
    """Load RSA private key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    
    with open(key_path, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend()
        )
    return private_key


def sign_request(private_key, timestamp: str, method: str, path: str) -> str:
    """Sign request with RSA-SHA256 using PSS padding (Kalshi requirement)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    
    # Strip query parameters from path before signing
    path_without_query = path.split('?')[0]
    message = f"{timestamp}{method}{path_without_query}"
    
    # Kalshi requires PSS padding
    signature = private_key.sign(
        message.encode('utf-8'),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode('utf-8')


def make_request(method: str, endpoint: str, private_key, api_key: str):
    """Make authenticated request to Kalshi API."""
    base_url = PRODUCTION_URL if USE_PRODUCTION else SANDBOX_URL
    path = f"/trade-api/v2{endpoint}"
    url = f"{base_url}{endpoint}"
    
    # Generate timestamp and signature
    timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    signature = sign_request(private_key, timestamp, method.upper(), path)
    
    # Build request
    req = urllib.request.Request(url, method=method)
    req.add_header("KALSHI-ACCESS-KEY", api_key)
    req.add_header("KALSHI-ACCESS-SIGNATURE", signature)
    req.add_header("KALSHI-ACCESS-TIMESTAMP", timestamp)
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        print(f"Response: {e.read().decode()[:500]}")
        raise


def test_connection():
    """Test API connection."""
    base_url = PRODUCTION_URL if USE_PRODUCTION else SANDBOX_URL
    env_name = "PRODUCTION" if USE_PRODUCTION else "SANDBOX"
    
    print("=" * 60)
    print(f"Kalshi API Connection Test ({env_name})")
    print("=" * 60)
    print(f"URL: {base_url}")
    print()
    
    if not USE_PRODUCTION:
        print("⚠️  NOTE: Demo and Production require SEPARATE API keys!")
        print("   If you get 401 errors, your API key may be for production.")
        print("   Create a demo account at demo.kalshi.com for demo API key.")
        print()
    
    # Check credentials
    if not API_KEY:
        print("❌ KALSHI_API_KEY not found in ~/.openclaw/.env")
        return False
    print(f"✓ API Key found: {API_KEY[:8]}...")
    
    if not PRIVATE_KEY_PATH.exists():
        print(f"❌ Private key not found: {PRIVATE_KEY_PATH}")
        return False
    print(f"✓ Private key found: {PRIVATE_KEY_PATH}")
    
    # Load private key
    try:
        private_key = load_private_key(PRIVATE_KEY_PATH)
        print("✓ Private key loaded successfully")
    except Exception as e:
        print(f"❌ Failed to load private key: {e}")
        return False
    
    # Test API call - Get exchange status
    print("\nTesting API connection...")
    try:
        status = make_request("GET", "/exchange/status", private_key, API_KEY)
        print(f"✓ Exchange status: {json.dumps(status, indent=2)}")
    except Exception as e:
        print(f"❌ Failed to get exchange status: {e}")
        return False
    
    # Test getting balance
    print("\nFetching account balance...")
    try:
        balance = make_request("GET", "/portfolio/balance", private_key, API_KEY)
        print(f"✓ Balance: {json.dumps(balance, indent=2)}")
    except Exception as e:
        print(f"❌ Failed to get balance: {e}")
        return False
    
    # Test getting markets
    print("\nFetching sample markets...")
    try:
        markets = make_request("GET", "/markets?status=open&limit=3", private_key, API_KEY)
        market_list = markets.get("markets", [])
        print(f"✓ Found {len(market_list)} markets")
        
        for m in market_list[:3]:
            print(f"   - {m.get('ticker')}: {m.get('title', '')[:50]}")
    except Exception as e:
        print(f"❌ Failed to get markets: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ All API tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
