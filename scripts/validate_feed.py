#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from scan_market import validate_feed


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_feed.py data/candidates.json")
    path = Path(sys.argv[1])
    validate_feed(json.loads(path.read_text(encoding="utf-8")))
    print(f"validated {path}")


if __name__ == "__main__":
    main()
