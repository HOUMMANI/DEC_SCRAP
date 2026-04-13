#!/usr/bin/env bash
# setup.sh - First-time setup for the Decypha Download Agent
set -e

echo "=== Decypha Download Agent - Setup ==="

# 1. Install Python dependencies
echo "[1/3] Installing Python packages..."
pip install -r requirements.txt

# 2. Install Playwright browser
echo "[2/3] Installing Playwright Chromium browser..."
playwright install chromium

# 3. Create .env if not already present
if [ ! -f .env ]; then
    echo "[3/3] Creating .env from template..."
    cp .env.example .env
    echo "  -> Please edit .env and fill in your Decypha credentials."
else
    echo "[3/3] .env already exists, skipping."
fi

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env and set DECYPHA_EMAIL and DECYPHA_PASSWORD"
echo "  2. Run:  python agent.py --list-sections"
echo "  3. Run:  python agent.py --section financials"
echo "  4. Or:   python agent.py --all"
