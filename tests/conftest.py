"""pytest config — ensure the harvester root is on sys.path so tests can
import top-level modules (claims_db, drift_monitor, watchdog, etc.)."""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
