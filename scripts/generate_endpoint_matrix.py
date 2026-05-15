#!/usr/bin/env python3
"""Generate the endpoint registry matrix block for endpoint_reference.md.

Usage:
    # Print generated block to stdout (dry-run)
    python3 scripts/generate_endpoint_matrix.py

    # Update docs/operations/endpoint_reference.md in-place
    python3 scripts/generate_endpoint_matrix.py --update
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from ai_model_serving.api.endpoint_matrix import generate_block, update_doc  # noqa: E402

_DOC_PATH = _ROOT / "docs" / "operations" / "endpoint_reference.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="Update endpoint_reference.md in-place")
    args = parser.parse_args()

    if args.update:
        changed = update_doc(_DOC_PATH)
        print("Updated." if changed else "No changes.")
    else:
        print(generate_block())


if __name__ == "__main__":
    main()
