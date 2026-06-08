"""Pytest configuration for backend tests.

Adds the project root to sys.path so tests can import from shared/.
"""
import sys
from pathlib import Path

# Add project root (where shared/ lives) to sys.path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
