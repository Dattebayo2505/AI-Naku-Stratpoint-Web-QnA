"""`python -m stratpoint_rag.evaluation` — run every registered layer, print the
table, exit non-zero if any non-skipped layer is below its floor (D3)."""

from __future__ import annotations

import sys

from stratpoint_rag.evaluation.harness import below_floor, format_table, run_all


def main(argv: list[str] | None = None) -> int:
    results = run_all()
    print(format_table(results))
    breached = [r for r in results if below_floor(r)]
    if breached:
        print(f"\n{len(breached)} layer(s) below floor: {', '.join(r.name for r in breached)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
