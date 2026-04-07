import os
import sys

# Ensure the 'src' directory is in the Python path for relative imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.tools.llm.generator import LMStudioLLMGenerator
from src.utils.logger import get_logger

log = get_logger(__name__)


def main():
    print("\n" + "=" * 50)
    print("DEBUGGING LLM GENERATOR (LMStudio @ http://localhost:1234)")
    print("=" * 50 + "\n")

    # Initialize client
    client = LMStudioLLMGenerator(base_url="http://localhost:1234/v1")

    # Basic Test 1: Simple greeting
    prompt = "Say 'Hello, the developer setup is working!' in a friendly tone."
    log.info("Test 1: Simple Greeting ...")

    try:
        response = client.generate(
            prompt, system_prompt="You are a helpful coding assistant.", temperature=0.7
        )
        print("-" * 30)
        print("GENESIS 1: Simple Greet Response")
        print("-" * 30)
        print(f"\n{response}\n")
        print("-" * 30)
    except Exception as e:
        print(f"❌ Test 1 Failed: {e}")
        print(
            "Is LMStudio running on port 1234? Ensure 'Server -> Start Server' is active."
        )
        return

    # Basic Test 2: Quick knowledge check
    prompt = "Explain in one sentence what a RAG (Retrieval-Augmented Generation) pipeline does."
    log.info("Test 2: Knowledge Check ...")

    try:
        response = client.generate(prompt)
        print("\n" + "-" * 30)
        print("GENESIS 2: RAG Explanation")
        print("-" * 30)
        print(f"\n{response}\n")
        print("-" * 30)
    except Exception as e:
        print(f"❌ Test 2 Failed: {e}")

    print("\n" + "=" * 50)
    print("DEBUG COMPLETE.")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    main()
