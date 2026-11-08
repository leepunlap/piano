"""Run every test module.

Usage:  python3 -m tests.run_all
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import test_pipeline, test_tracker

MODULES = [test_pipeline, test_tracker]


def main() -> int:
    total_passed = 0
    total_failed = 0

    for module in MODULES:
        result = module.main()
        total_passed += len(module.PASSED)
        total_failed += len(module.FAILED)
        print()

    print("=" * 68)
    print(f"TOTAL: {total_passed} passed, {total_failed} failed")
    print("=" * 68)
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
