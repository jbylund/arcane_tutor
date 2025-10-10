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
python -m pip install --upgrade pip
python -m pip install uv

# Create virtual environment
echo "Creating virtual environment..."
python -m uv venv .venv

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Install Python dependencies in virtual environment
echo "Installing Python dependencies..."
uv pip install -r requirements/base.txt -r requirements/test.txt

echo "Setup complete! Environment ready for Scryfall OS development."
echo "Virtual environment created at .venv"
echo "To activate the virtual environment, run: source .venv/bin/activate"
