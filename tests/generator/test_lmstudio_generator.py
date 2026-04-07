import json
from unittest.mock import MagicMock, patch

import pytest

from src.tools.llm.generator import LMStudioLLMGenerator


def test_lmstudio_generator_label():
    generator = LMStudioLLMGenerator()
    assert generator.label == "lmstudio"
    assert "<LLMGenerator:lmstudio>" in repr(generator)


@patch("urllib.request.urlopen")
def test_lmstudio_generate_success(mock_urlopen):
    # Prepare dummy successful response
    mock_response = MagicMock()
    mock_response.status = 200

    mock_json_response = {
        "choices": [
            {"message": {"role": "assistant", "content": "Test response from LLM"}}
        ]
    }

    mock_response.read.return_value = json.dumps(mock_json_response).encode("utf-8")
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    generator = LMStudioLLMGenerator(base_url="http://mock-url:1234/v1")
    response = generator.generate("Hello", system_prompt="Test system")

    assert response == "Test response from LLM"

    # Verify the values sent in the request
    args, kwargs = mock_urlopen.call_args
    req = args[0]

    assert req.full_url == "http://mock-url:1234/v1/chat/completions"
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"

    sent_data = json.loads(req.data.decode("utf-8"))
    assert sent_data["messages"][0]["content"] == "Test system"
    assert sent_data["messages"][1]["content"] == "Hello"


@patch("urllib.request.urlopen")
def test_lmstudio_generate_server_error(mock_urlopen):
    # Prepare server error response
    mock_response = MagicMock()
    mock_response.status = 500
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    generator = LMStudioLLMGenerator()

    with pytest.raises(ValueError, match="Server returned status 500"):
        generator.generate("Hello")


@patch("urllib.request.urlopen")
def test_lmstudio_generate_malformed_response(mock_urlopen):
    # Prepare malformed response (no choices)
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b'{"status": "ok"}'  # Missing choices
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    generator = LMStudioLLMGenerator()

    with pytest.raises(ValueError, match="Malformed response from local server"):
        generator.generate("Hello")


def test_generate_from_template_file_not_found():
    generator = LMStudioLLMGenerator()
    with pytest.raises(FileNotFoundError, match="Prompt template not found"):
        generator.generate_from_template("non_existent_template", variables={})


def test_generator_registry():
    from src.tools.llm.generator import LMStudioLLMGenerator, get_generator

    # Successful retrieval
    gen = get_generator("lmstudio", base_url="http://localhost:1234")
    assert isinstance(gen, LMStudioLLMGenerator)
    assert gen.base_url == "http://localhost:1234"

    # Unknown label
    with pytest.raises(ValueError, match="Unsupported generator: unknown"):
        get_generator("unknown")
