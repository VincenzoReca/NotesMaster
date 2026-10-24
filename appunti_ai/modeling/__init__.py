"""Profili e adapter per l'accesso ai modelli senza accoppiamento ai provider."""

from .client import AzureOpenAIClient, ModelClient, ModelRequest, ModelResponse, create_client
from .profiles import ModelProfile, load_profile, load_profiles

__all__ = [
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "ModelProfile",
    "create_client",
    "load_profile",
    "load_profiles",
]
