import os
import sys

# Ensure the project root is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path

from src.tools.knowledge_base.ingester import ClaimIngester
from src.tools.llm.generator import get_generator
from src.utils.logger import get_logger

log = get_logger(__name__)


def main() -> None:
    print("\n" + "=" * 60)
    print("CLAIM INGESTION PIPELINE")
    print("=" * 60 + "\n")

    # ── Configure ──────────────────────────────────────────────────
    claim_id = "1"  # Change this to test different claims
    claim_dir = Path(__file__).parent / "cases" / claim_id

    if not claim_dir.exists():
        print(f"❌ Claim folder not found: {claim_dir}")
        print("   Create it and place supported documents (.pdf, .docx) inside.")
        return

    # Select generator backend (default to lmstudio)
    # Available: 'lmstudio', 'huggingface'
    llm = get_generator("lmstudio")
    ingester = ClaimIngester(claim_dir=claim_dir, llm=llm)

    # ── Run ────────────────────────────────────────────────────────
    try:
        ingester.run()

        processed_dir = ingester.processed_dir
        print(f"\n✅ Ingestion complete. Output files in:\n   {processed_dir}\n")
        print(f"   📄 diary.md         — {ingester.diary_path.stat().st_size} bytes")
        print(f"   📋 ledger.md        — {ingester.ledger_path.stat().st_size} bytes")
        sz = (
            ingester.summary_path.stat().st_size
            if ingester.summary_path.exists()
            else 0
        )
        print(f"   📊 summary_table.md — {sz} bytes")

    except Exception as e:
        print(f"❌ Ingestion failed: {e}")
        import traceback

        traceback.print_exc()

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
