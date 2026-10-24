import unittest
from pathlib import Path

from appunti_ai.modeling import ModelRequest, create_client, load_profile, load_profiles
from appunti_ai.modeling.client import AzureOpenAIClient, OllamaClient, OpenAICompatibleClient
from appunti_ai.modeling.profiles import ModelProfile, ProfileError
from appunti_ai.generation.notes import make_prompt


class ModelingTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_generation_prompt_can_include_shared_context(self):
        prompt = make_prompt("primary_lecture", "lecture.md", 1, "Routing IP", False, "[Fonte condivisa: shared.md]\nSubnet mask")
        self.assertIn("PASSAGGI PERTINENTI DA FONTI CONDIVISE", prompt)
        self.assertIn("Subnet mask", prompt)

    def test_generation_prompt_requires_image_explanation_and_explicit_marker(self):
        prompt = make_prompt("professor_slides", "slides.pdf", 1, "", True)
        self.assertIn("IMAGE REQUIREMENT", prompt)
        self.assertIn("VISUAL INTERPRETATION", prompt)

    def test_default_profile_is_explicit_and_capability_aware(self):
        default_name, profiles = load_profiles(self.ROOT / "model_profiles.json")
        profile = load_profile(None, self.ROOT / "model_profiles.json")

        self.assertEqual(default_name, "local_qwen")
        self.assertEqual(profile.name, "local_qwen")
        self.assertIn("text", profile.capabilities)
        self.assertIn("vision", profile.capabilities)
        self.assertIsInstance(profiles[default_name], ModelProfile)

    def test_ollama_adapter_is_selected_without_contacting_runtime(self):
        profile = load_profile("local_qwen", self.ROOT / "model_profiles.json")

        client = create_client(profile)

        self.assertIsInstance(client, OllamaClient)

    def test_remote_adapter_requires_explicit_opt_in(self):
        profile = ModelProfile(
            name="remote_test",
            backend="openai_compatible",
            model="test-model",
            capabilities=frozenset({"text"}),
            options={},
            remote=True,
            endpoint="http://127.0.0.1:9/v1",
        )
        with self.assertRaisesRegex(ProfileError, "opt-in"):
            create_client(profile)

    def test_azure_adapter_is_configurable_without_network_call(self):
        profile = ModelProfile(name="azure", backend="azure_openai", model="gpt-test", capabilities=frozenset({"text"}), options={}, remote=True, endpoint="https://example.openai.azure.com", api_key_env="AZURE_TEST_KEY", deployment="demo", api_version="2024-10-21")
        client = create_client(profile, allow_remote=True)
        self.assertIsInstance(client, AzureOpenAIClient)
        self.assertIn("/openai/deployments/demo/chat/completions", client.endpoint)
        self.assertIsInstance(create_client(profile, allow_remote=True), OpenAICompatibleClient)

    def test_capability_preflight_rejects_missing_vision(self):
        profile = ModelProfile(
            name="text_only",
            backend="ollama",
            model="text-model",
            capabilities=frozenset({"text"}),
            options={},
        )
        client = create_client(profile)
        with self.assertRaisesRegex(ProfileError, "vision"):
            client.generate(ModelRequest(messages=[], required_capabilities=frozenset({"text", "vision"})))


if __name__ == "__main__":
    unittest.main()
