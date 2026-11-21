"""Replay short-answer fallback on known paired generation results."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from evals.metrics import case_outcome
from evals.run_web_baseline import distribution

RETRY = {"partial", "invalid", "generation_error"}


def evaluate(pairs: dict) -> dict:
    decisions = {}
    for case_id, pair in pairs.items():
        fast = pair["without_reasoning"]
        control = pair["control"]
        decisions[case_id] = [fast, control] if fast["status"] in RETRY else [fast]
    schedules = {
        "control": {id_: [pair["control"]] for id_, pair in pairs.items()},
        "fast": {id_: [pair["without_reasoning"]] for id_, pair in pairs.items()},
        "cascade": decisions,
    }
    reports = {}
    for name, schedule in schedules.items():
        calls = []
        latencies = []
        outcomes = {}
        for case_id, answers in schedule.items():
            outcomes[case_id] = case_outcome(answers[-1])
            generations = [row["generation"] for row in answers if row.get("generation")]
            calls.extend(generations)
            latencies.append(sum(call["latency_ms"] for call in generations))
        costs = [
            Decimal(str(call["usage"]["cost"]))
            for call in calls
            if isinstance(call["usage"].get("cost"), int | float)
        ]
        reports[name] = {
            "outcomes": dict(Counter(outcomes.values())),
            "strict_ids": sorted(
                id_ for id_, outcome in outcomes.items() if outcome == "strict_pass"
            ),
            "requests": len(calls),
            "requests_with_cost": len(costs),
            "generation_cost_rub": str(sum(costs)) if costs else None,
            "generation_latency_ms": distribution(latencies),
        }
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paired_report", type=Path)
    args = parser.parse_args()
    pairs = json.loads(args.paired_report.read_text(encoding="utf-8"))["pairs"]
    print(json.dumps(evaluate(pairs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
