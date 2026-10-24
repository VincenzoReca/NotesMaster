"""Entry point compatibile per il consolidamento delle bozze.

La logica è in :mod:`appunti_ai.consolidation.notes`.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appunti_ai.consolidation.notes import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
