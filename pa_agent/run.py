#!/usr/bin/env python3
"""
ClipButler service launcher.
Run from the pa_agent/ directory:

    python run.py
    python run.py --setup
    python run.py --port 8765 --config /path/to/config.json
"""

import sys
import os

# Ensure pa_agent/ is on sys.path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.main import main

if __name__ == "__main__":
    main()
