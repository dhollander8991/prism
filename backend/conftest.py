import sys
from pathlib import Path

# Ensure backend/ is on the path so tests can import connectors, db, etc.
sys.path.insert(0, str(Path(__file__).parent))
