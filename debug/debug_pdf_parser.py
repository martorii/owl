import os
import sys

# Ensure the 'src' directory is in the Python path for relative imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from src.tools.parser.models import Document
    from src.tools.parser.parser import parse_document
    # This also triggers the imports in parser.py: from tools.parser.models...
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)


def main():
    # Define target_file
    target_file = "contract.pdf"
    docs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "docs"))
    file_path = os.path.join(docs_dir, target_file)

    if not os.path.exists(file_path):
        print(f"File not found: {target_file}")
        return

    print("\n" + "=" * 40)
    print(f"PARSING PDF DOCUMENT: {target_file}")
    print("=" * 40 + "\n")

    try:
        with open(file_path, "rb") as f:
            data = f.read()

        # Create Document container
        doc = Document(
            filename=target_file,
            extension=target_file.rsplit(".", 1)[-1].lower()
            if "." in target_file
            else "",
            data=data,
        )

        # Parse the document: this will populate doc.chunks in-place
        parse_document(doc)

        # Print the beautiful representation
        print(doc)
        print("\n" + "=" * 40 + "\n")

        # Print the consolidated content
        print("CONSOLIDATED CONTENT")
        print("=" * 40 + "\n")
        print(doc.get_content())
        print("=" * 40 + "\n")

    except Exception as e:
        print(f"❌ Error while parsing {target_file}: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
