#!/bin/bash
set -e

# Define a local virtual environment path (outside of iCloud)
VENV_PATH="$HOME/.local/share/tiktok-tweets-venv"

echo "Setting up environment in $VENV_PATH..."

# Create venv if it doesn't exist
if [ ! -d "$VENV_PATH" ]; then
    uv venv "$VENV_PATH"
fi

# Install dependencies into this specific environment
source "$VENV_PATH/bin/activate"
uv pip install fastapi uvicorn sqlalchemy httpx openai jinja2 python-dotenv python-multipart requests

echo "Starting server..."
python -m uvicorn receiver:app --port 8001
