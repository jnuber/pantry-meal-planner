#!/bin/bash
# Navigate to project directory
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Run streamlit and open browser
streamlit run app.py
