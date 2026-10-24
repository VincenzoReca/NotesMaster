"""Compatibilità per l'API di estrazione isolata.

La logica risiede in :mod:`appunti_ai.extraction.scoped`.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appunti_ai.extraction.scoped import (
    EXTRACTION_VERSION,
    SUPPORTED_EXTENSIONS,
    ExtractionError,
    ExtractionScope,
    build_scope,
    extract_scope,
)

__all__ = [
    "EXTRACTION_VERSION",
    "SUPPORTED_EXTENSIONS",
    "ExtractionError",
    "ExtractionScope",
    "build_scope",
    "extract_scope",
]
