import sys
from pathlib import Path

# Permet `import smc_collector` sans installation (pas de packaging pour ce projet).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
