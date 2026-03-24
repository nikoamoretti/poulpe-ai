#!/bin/bash
# Load environment variables from .env
set -a
source /Users/nico-yardlogix/poulpe-ai/backend/.env
set +a

# Ensure claude CLI and node are on PATH
export PATH="/Users/nico-yardlogix/.local/bin:/Users/nico-yardlogix/.bun/bin:/Users/nico-yardlogix/.npm-global/bin:/opt/homebrew/bin:$PATH"

exec /Users/nico-yardlogix/poulpe-ai/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
