from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

import requests

from src.utils.logger import get_logger

log = get_logger(__name__)


# ─── Base class ───────────────────────────────────────────────────────────────


class LLMGenerator(ABC):
    """
    Abstract base class for all LLM generator implementations.
    """

    label: str = "abstract"

    def __init__(self):
        # Path to prompt templates relative to this file
        self.template_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
        )

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        temperature: float = 0.7,
        max_tokens: int = -1,
        model: str = "local-model",
        **kwargs: Any,
    ) -> str:
        """
        Send a prompt to the LLM and return the generated text.
        """
        pass

    def generate_from_template(
        self, template_name: str, variables: dict[str, Any], **kwargs: Any
    ) -> str:
        """
        Load a markdown template file, replace double-brace placeholders, and generate content.

        Parameters
        ----------
        template_name:
            File name without extension in src/prompts/templates/ (e.g., 'rag_qa').
        variables:
            Dictionary with keys matching the {{placeholder}} names in the file.
        **kwargs:
            Direct arguments passed to self.generate (temperature, system_prompt, etc.).
        """
        # Load template
        tpl_path = os.path.join(self.template_dir, f"{template_name}.md")

        if not os.path.exists(tpl_path):
            raise FileNotFoundError(f"Prompt template not found: {tpl_path}")

        with open(tpl_path, encoding="utf-8") as f:
            template_str = f.read()

        # Simple string formatting for {{placeholder}}
        final_prompt = template_str
        for key, value in variables.items():
            final_prompt = final_prompt.replace(f"{{{{{key}}}}}", str(value))

        # Partial validation of remaining placeholders
        if "{{" in final_prompt:
            log.warning(
                "Some placeholders might not have been replaced in the template: %s",
                template_name,
            )

        log.debug("Loaded and formatted template %s", template_name)

        # Log final prompt at DEBUG level
        if log.level <= 10:  # DEBUG level is 10
            log.debug("\n" + "=" * 40)
            log.debug("FINAL PROMPT: %s", template_name)
            log.debug("=" * 40 + "\n")
            log.debug(final_prompt)
            log.debug("=" * 40 + "\n")

        log.debug("Rough number of tokens: %d", len(final_prompt) // 4)
        return self.generate(final_prompt, **kwargs)

    def __repr__(self) -> str:
        return f"<LLMGenerator:{self.label}>"


# ─── Implementations ──────────────────────────────────────────────────────────


class LMStudioLLMGenerator(LLMGenerator):
    """
    Client for interacting with local LLMs via OpenAI-compatible endpoints
    (e.g., LM Studio, Ollama, vLLM).
    """

    label: str = "lmstudio"

    def __init__(self, base_url: str = "http://localhost:1234/v1"):
        super().__init__()
        self.base_url = base_url.rstrip("/")

    def generate(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        temperature: float = 0.7,
        max_tokens: int = -1,
        model: str = "local-model",
        **kwargs: Any,
    ) -> str:
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        log.info("Generating response from local LLM at %s ...", url)

        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=2000) as response:
                if response.status != 200:
                    raise ValueError(f"Server returned status {response.status}")

                resp_body = response.read().decode("utf-8")
                result = json.loads(resp_body)

                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    log.debug("LLM generation successful.")
                    return str(content)
                else:
                    log.error("Invalid response format from LLM: %s", result)
                    raise ValueError("Malformed response from local server.")

        except urllib.error.URLError as e:
            log.error("Connection to local LLM failed: %s", e)
            raise ConnectionError(
                f"Could not connect to generator at {url}. Is the server running?"
            ) from e
        except Exception as e:
            log.error("Unexpected error during LLM generation: %s", e)
            raise


class HuggingFaceGenerator(LLMGenerator):
    """
    Client for interacting with Hugging Face Inference Endpoints.
    """

    label: str = "huggingface"

    def __init__(
        self,
        token: str | None = None,
        default_model: str = "nvidia/Gemma-4-31B-IT-NVFP4",
    ):
        super().__init__()
        self.token = token or os.environ.get("HF_TOKEN")
        if not self.token:
            log.warning("HF_TOKEN environment variable not set. Requests may fail.")
        self.default_model = default_model

    def generate(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        temperature: float = 0.7,
        max_tokens: int = -1,
        model: str | None = None,
        **kwargs: Any,
    ) -> str:
        model_id = model or self.default_model
        url = "https://router.huggingface.co/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [{"type": "text", "text": prompt}]},
            ],
            "model": model_id,
            "temperature": temperature,
        }

        if max_tokens > 0:
            payload["max_tokens"] = max_tokens

        log.info(
            "Generating response from Hugging Face LLM (%s) at %s ...", model_id, url
        )

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=2000)
            response.raise_for_status()
            result = response.json()

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                log.debug("LLM generation successful.")
                return str(content)
            else:
                log.error("Invalid response format from HF LLM: %s", result)
                raise ValueError("Malformed response from Hugging Face server.")

        except requests.exceptions.RequestException as e:
            log.error("Connection to Hugging Face LLM failed: %s", e)
            if hasattr(e.response, "text"):
                log.error("Error details: %s", e.response.text)
            raise ConnectionError(f"Could not connect to generator at {url}.") from e
        except Exception as e:
            log.error("Unexpected error during LLM generation: %s", e)
            raise


# ─── Registry ──────────────────────────────────────────────────────────

_GENERATORS: dict[str, type[LLMGenerator]] = {
    "lmstudio": LMStudioLLMGenerator,
    "huggingface": HuggingFaceGenerator,
}


def get_generator(label: str, **kwargs: Any) -> LLMGenerator:
    """
    Factory function to get a specific generator instance.
    """
    if label not in _GENERATORS:
        raise ValueError(
            f"Unsupported generator: {label}. Available: {list(_GENERATORS.keys())}"
        )

    return _GENERATORS[label](**kwargs)
