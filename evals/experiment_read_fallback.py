"""Переигрывает чтение источника после неполного ответа."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from evals.metrics import case_outcome
from evals.run_web_baseline import usage_summary


def read_pairs(report: dict) -> list[tuple[dict, dict]]:
    if "pairs" in report:
        return [(pair["control"], pair["treatment"]) for pair in report["pairs"]]
    answers = report["answer_runs"]
    return list(zip(answers["control"], answers["markdown"], strict=True))


def measure(
    pairs: list[tuple[dict, dict]],
    *,
    statuses: tuple[str, ...] = ("partial", "unverified"),
) -> dict:
    chosen = []
    reads = []
    transitions = []
    for direct, read in pairs:
        if direct["id"] != read["id"]:
            raise ValueError("Пары ответов не совпадают по ID.")
        eligible = direct["status"] in statuses
        if eligible:
            reads.append(read)
        final = read if eligible and read["status"] == "answered" else direct
        chosen.append(final)
        transitions.append(
            {
                "id": direct["id"],
                "read": eligible,
                "before": case_outcome(direct),
                "after": case_outcome(final),
            }
        )
    direct_rows = [direct for direct, _ in pairs]
    costs = [
        usage_summary(rows, "generation")["reported_cost_rub"] for rows in (direct_rows, reads)
    ]
    control_cost, retry_cost = [Decimal(value) if value is not None else None for value in costs]
    return {
        "cases": len(pairs),
        "reads": len(reads),
        "before": dict(Counter(map(case_outcome, direct_rows))),
        "after": dict(Counter(map(case_outcome, chosen))),
        "new_strict": sum(
            r["before"] != "strict_pass" and r["after"] == "strict_pass" for r in transitions
        ),
        "lost_strict": sum(
            r["before"] == "strict_pass" and r["after"] != "strict_pass" for r in transitions
        ),
        "new_critical_error": sum(
            r["before"] != "critical_error" and r["after"] == "critical_error" for r in transitions
        ),
        "control_generation_rub": str(control_cost) if control_cost is not None else None,
        "extra_generation_rub": str(retry_cost) if retry_cost is not None else None,
        "rows": transitions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    only = parser.add_mutually_exclusive_group()
    only.add_argument(
        "--unverified-only",
        action="store_const",
        const=("unverified",),
        dest="statuses",
    )
    only.add_argument("--partial-only", action="store_const", const=("partial",), dest="statuses")
    parser.set_defaults(statuses=("partial", "unverified"))
    args = parser.parse_args()
    result = {
        path.parent.name: measure(
            read_pairs(json.loads(path.read_text(encoding="utf-8"))),
            statuses=args.statuses,
        )
        for path in args.reports
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
