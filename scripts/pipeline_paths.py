"""Compatibilità CLI per i contratti di percorso.

La logica risiede in :mod:`appunti_ai.contracts.paths`.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appunti_ai.contracts.paths import (
    DEFAULT_INPUT_DIRNAME,
    DEFAULT_OUTPUT_DIRNAME,
    SECTION_PREFIX,
    SHARED_SOURCES_DIRNAME,
    SUBJECT_CONFIG_FILENAME,
    DiscoveryIssue,
    PathContractError,
    SectionPaths,
    SubjectPaths,
    discover_subject,
    discover_subject_names,
    ensure_within,
    load_subject_config,
    main,
    project_paths,
)

__all__ = [
    "DEFAULT_INPUT_DIRNAME",
    "DEFAULT_OUTPUT_DIRNAME",
    "SECTION_PREFIX",
    "SHARED_SOURCES_DIRNAME",
    "SUBJECT_CONFIG_FILENAME",
    "DiscoveryIssue",
    "PathContractError",
    "SectionPaths",
    "SubjectPaths",
    "discover_subject",
    "discover_subject_names",
    "ensure_within",
    "load_subject_config",
    "main",
    "project_paths",
]


if __name__ == "__main__":
    raise SystemExit(main())
