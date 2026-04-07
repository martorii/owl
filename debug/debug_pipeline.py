import os
import sys

# Ensure the 'src' directory is in the Python path for relative imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.tools.llm.generator import LMStudioLLMGenerator
from src.tools.parser.models import Document
from src.tools.parser.parser import parse_document
from src.utils.logger import get_logger

log = get_logger(__name__)


def main():
    print("\n" + "=" * 60)
    print("PIPELINE TEST (PARSER + LLM TEMPLATE)")
    print("=" * 60 + "\n")

    # 1. SETUP - Choose a target file and LLM client
    target_file = "contract.pdf"
    docs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "docs"))
    file_path = os.path.join(docs_dir, target_file)

    if not os.path.exists(file_path):
        print(f"❌ File not found: {target_file} in {docs_dir}")
        return

    llm = LMStudioLLMGenerator(base_url="http://localhost:1234/v1")

    # 2. PARSE - Extract content from the document
    log.info("Step 1: Parsing %s ...", target_file)
    try:
        with open(file_path, "rb") as f:
            data = f.read()

        doc = Document(filename=target_file, extension="pdf", data=data)

        parse_document(doc)
        context = doc.get_content()
        log.info("Parsing complete. %d chunks extracted.", len(doc.chunks))

    except Exception as e:
        print(f"❌ Extraction Failed: {e}")
        return

    # 3. GENERATE - Ask the LLM using a markdown template
    log.info("Step 2: Asking question to LLM using template ...")

    # Hard-coded question for testing
    question = "Was ist die Mindestlaufzeit von dem KfW Kredit?"

    try:
        response = llm.generate_from_template(
            template_name="qa",
            variables={"question": question, "context": context},
            system_prompt="You are an expert at analyzing documents regardless of their language and complexity.",
            temperature=0.3,
        )

        print("\n" + "-" * 60)
        print(f"QUESTION: {question}")
        print("-" * 60)
        print(f"\n{response}\n")
        print("-" * 60)

    except Exception as e:
        print(f"❌ LLM Generation Failed: {e}")
        print("Ensure LMStudio is running at localhost:1234 and template exists.")

    print("\n" + "=" * 60)
    print("PIPELINE TEST COMPLETE.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
