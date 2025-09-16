#!/bin/bash
# Copilot environment setup script
# Pre-installs system dependencies and Python requirements for Scryfall OS development

set -e  # Exit on any error

echo "Setting up Scryfall OS development environment for Copilot..."

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y libev-dev

# Install uv for faster package management
echo "Installing uv..."
# Try the official installer first, fallback to pip if needed
if curl -LsSf https://astral.sh/uv/install.sh | sh; then
    source $HOME/.cargo/env
else
    echo "Installing uv via pip as fallback..."
    python -m pip install --upgrade pip
    python -m pip install uv
fi

# Install Python dependencies
echo "Installing Python dependencies..."
uv pip install --system --break-system-packages -r requirements.txt -r test-requirements.txt

echo "Setup complete! Environment ready for Scryfall OS development."
