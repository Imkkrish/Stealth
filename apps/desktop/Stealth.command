#!/bin/bash
cd "$(dirname "$0")"
export GEMINI_API_KEY=$(cat .env 2>/dev/null | grep GEMINI_API_KEY | cut -d'=' -f2)
source venv/bin/activate 2>/dev/null
npm run start-electron
