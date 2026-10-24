
"""Entry point compatibile per il smoke test visivo di Ollama.

Non effettua chiamate al modello quando viene importato dai test.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appunti_ai.diagnostics.vision_smoke import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
