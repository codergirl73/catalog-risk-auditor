#!/usr/bin/env python3
"""Entrypoint.

    python3 run.py data/catalog --royalties data/royalties.csv \
        --truth data/ground_truth.csv --json
"""

from catalog_audit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
