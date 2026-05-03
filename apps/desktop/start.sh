#!/bin/bash

# Check for GEMINI_API_KEY
if [ -z "$GEMINI_API_KEY" ]; then
    echo "WARNING: GEMINI_API_KEY is not set. The AI features will not work."
    echo "Please export GEMINI_API_KEY='your_key_here' before running."
    echo "Continuing in 5 seconds..."
    sleep 5
fi

# Install Python dependencies
# Setup Python environment
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Fix SSL Certificate issues on Mac & Install Dependencies
echo "Installing/Updating Python dependencies..."
pip install --upgrade pip setuptools wheel certifi
export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")
pip install -r src/python/requirements.txt

# Install Node dependencies
if [ ! -d "node_modules" ]; then
    echo "Installing Node dependencies..."
    npm install
fi

# Start the application
echo "Starting Stealth..."
npm run dev
