"""Replay fixed answer branches with a live Jev routing decision."""

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.config import load_settings
from evals.experiment_decisions import ABSTAIN, decide
from evals.metrics import case_outcome
from evals.provider_settings import routerai_key
from evals.run_web_baseline import distribution


def routing_case(row: dict, can_read: bool) -> dict:
    return {
        "id": row["id"],
        "phase": "evidence",
        "state": {
            "question": row["search"]["query"],
            "fragments": row["sources"],
            "can_read": can_read,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reading", type=Path)
    parser.add_argument("grounding", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    reading = json.loads(args.reading.read_text(encoding="utf-8"))
    grounding = json.loads(args.grounding.read_text(encoding="utf-8"))
    settings = load_settings()
    api_key = routerai_key(settings)
    interface = os.getenv("ROUTERAI_NETWORK_INTERFACE") or settings.get(
        "ROUTERAI_NETWORK_INTERFACE", ""
    )
    pairs = reading["pairs"]
    missing_evidence = [row for row in grounding["runs"] if row["id"] in ABSTAIN]
    inputs = [routing_case(pair["control"], True) for pair in pairs] + [
        routing_case(row, False) for row in missing_evidence
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        decisions = dict(
            zip(
                (row["id"] for row in inputs),
                pool.map(lambda row: decide(row, "jev", api_key, interface), inputs),
                strict=True,
            )
        )
    comparisons = []
    for pair in pairs:
        case_id = pair["id"]
        choice = decisions[case_id]["choice"]
        control = case_outcome(pair["control"])
        always = case_outcome(pair["treatment"])
        routed = (
            "unverified" if choice == "abstain" else always if choice == "read_more" else control
        )
        comparisons.append(
            {"id": case_id, "control": control, "always_read": always, "jev": routed}
        )
    cases = {
        key: Counter(row[key] for row in comparisons) for key in ("control", "always_read", "jev")
    }
    report = {
        "outcomes": cases,
        "selected_reads": sum(decisions[pair["id"]]["choice"] == "read_more" for pair in pairs),
        "wrong_evidence": {row["id"]: decisions[row["id"]]["choice"] for row in missing_evidence},
        "latency_ms": distribution([row["latency_ms"] for row in decisions.values()]),
        "decisions": decisions,
        "pairs": comparisons,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
