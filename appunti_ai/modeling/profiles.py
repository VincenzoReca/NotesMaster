"""Caricamento e validazione dei profili modello versionati."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_SCHEMA_VERSION = 1
DEFAULT_PROFILE_FILE = Path(__file__).resolve().parents[2] / "model_profiles.json"


def _load_local_env() -> None:
    """Load simple KEY=VALUE entries from the repository .env if present.

    Existing process environment variables always win. Values are never
    returned, logged, or written to state/audit files.
    """
    env_path = DEFAULT_PROFILE_FILE.parent / ".env"
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


class ProfileError(ValueError):
    """Profilo mancante, invalido o non compatibile."""


@dataclass(frozen=True)
class ModelProfile:
    name: str
    backend: str
    model: str
    capabilities: frozenset[str]
    options: dict[str, Any]
    remote: bool = False
    endpoint: str | None = None
    endpoint_env: str | None = None
    api_key_env: str | None = None
    deployment: str | None = None
    api_version: str | None = None

    def require(self, required: set[str] | frozenset[str]) -> None:
        missing = sorted(set(required) - self.capabilities)
        if missing:
            raise ProfileError(
                f"Il profilo {self.name!r} non supporta le capacità richieste: "
                + ", ".join(missing)
            )


def _validate_profile(name: str, value: Any) -> ModelProfile:
    if not isinstance(value, dict):
        raise ProfileError(f"Il profilo {name!r} deve essere un oggetto JSON.")
    backend = value.get("backend")
    model = value.get("model")
    capabilities = value.get("capabilities")
    options = value.get("options", {})
    if not isinstance(backend, str) or not backend.strip():
        raise ProfileError(f"Il profilo {name!r} richiede backend non vuoto.")
    if not isinstance(model, str) or not model.strip():
        raise ProfileError(f"Il profilo {name!r} richiede model non vuoto.")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise ProfileError(f"Il profilo {name!r} richiede capabilities come lista di stringhe.")
    if not isinstance(options, dict):
        raise ProfileError(f"Le options del profilo {name!r} devono essere un oggetto.")
    remote = value.get("remote", False)
    if not isinstance(remote, bool):
        raise ProfileError(f"Il campo remote del profilo {name!r} deve essere booleano.")
    endpoint = value.get("endpoint")
    endpoint_env = value.get("endpoint_env")
    api_key_env = value.get("api_key_env")
    deployment = value.get("deployment")
    api_version = value.get("api_version")
    for field_name, field_value in (("endpoint", endpoint), ("endpoint_env", endpoint_env), ("api_key_env", api_key_env), ("deployment", deployment), ("api_version", api_version)):
        if field_value is not None and (not isinstance(field_value, str) or not field_value.strip()):
            raise ProfileError(f"Il campo {field_name} del profilo {name!r} deve essere una stringa non vuota.")
    if remote and not endpoint and not endpoint_env:
        raise ProfileError(f"Il profilo remoto {name!r} richiede endpoint o endpoint_env.")
    return ModelProfile(
        name=name,
        backend=backend.strip(),
        model=model.strip(),
        capabilities=frozenset(capabilities),
        options=dict(options),
        remote=remote,
        endpoint=endpoint,
        endpoint_env=endpoint_env,
        api_key_env=api_key_env,
        deployment=deployment,
        api_version=api_version,
    )


def load_profiles(path: Path = DEFAULT_PROFILE_FILE) -> tuple[str, dict[str, ModelProfile]]:
    _load_local_env()
    if not path.is_file():
        raise ProfileError(f"File profili non trovato: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Impossibile leggere il file profili {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != PROFILE_SCHEMA_VERSION:
        raise ProfileError(f"Schema profili non supportato in {path}.")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ProfileError(f"Nessun profilo valido in {path}.")
    profiles = {name: _validate_profile(name, value) for name, value in raw_profiles.items()}
    default_name = document.get("default_profile")
    if not isinstance(default_name, str) or default_name not in profiles:
        raise ProfileError(f"default_profile non presente tra i profili di {path}.")
    return default_name, profiles


def load_profile(name: str | None, path: Path = DEFAULT_PROFILE_FILE) -> ModelProfile:
    default_name, profiles = load_profiles(path)
    selected = name or default_name
    try:
        return profiles[selected]
    except KeyError as exc:
        raise ProfileError(f"Profilo modello non trovato: {selected}") from exc
