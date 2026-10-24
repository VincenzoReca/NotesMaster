"""Compatibility entry point for legacy and scoped extraction."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appunti_ai.extraction.legacy import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
