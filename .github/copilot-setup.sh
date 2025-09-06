#!/bin/bash
# Copilot environment setup script
# Pre-installs system dependencies and Python requirements for Scryfall OS development

set -e  # Exit on any error

echo "Setting up Scryfall OS development environment for Copilot..."

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y libev-dev

# Install Python dependencies
echo "Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r test-requirements.txt
python -m pip install bjoern

echo "Setup complete! Environment ready for Scryfall OS development."