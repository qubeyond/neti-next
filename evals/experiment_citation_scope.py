"""Compare literal citation checks with a decision about evidence applicability."""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from agent.config import load_settings
from evals.experiment_decisions import decide
from evals.provider_settings import routerai_key
from evals.run_web_baseline import distribution

POSITIVE = (
    "nsu-library-login",
    "nstu-budget",
    "sib-post",
    "nspu-triple-price-gap",
    "nstu-business-practice-years",
    "nsu-eduroam-login",
)
NEGATIVE_WEB = ("nsu-certificate", "nstu-budget", "sib-post", "nspu-triple-price-gap")
CHOICES = {
    "publish": (
        "Ответ полностью отвечает на вопрос, и все существенные утверждения "
        "поддержаны цитатами в их контексте."
    ),
    "withhold": (
        "Ответ неполон, противоречит источнику, или цитаты относятся к другой "
        "сущности, услуге, дате, филиалу либо условиям."
    ),
}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def draft(row: dict, expected: str) -> dict:
    citations = row["citations"]
    if not citations or not all(citation["verified"] for citation in citations):
        raise ValueError(f"Expected literal quotations in {row['id']}.")
    return {
        "id": row["id"],
        "phase": "verification",
        "expected": expected,
        "state": {
            "question": row["search"]["query"],
            "draft_answer": row["answer"],
            "citations": [{"source": item["source"], "quote": item["quote"]} for item in citations],
            "cited_fragments": [
                source
                for source in row["sources"]
                if source["source"] in {c["source"] for c in citations}
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument("reasoning", type=Path)
    parser.add_argument("status", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    web = {row["id"]: row for row in read(args.baseline)["runs"]}
    oracle = {row["id"]: row for row in read(args.oracle)["runs"]}
    cases = [draft(oracle[case_id], "publish") for case_id in POSITIVE]
    cases.extend(draft(web[case_id], "withhold") for case_id in NEGATIVE_WEB)
    cases.append(draft(read(args.reasoning)["pairs"]["vki-start"]["control"], "withhold"))
    cases.append(draft(read(args.status)["variants"]["explicit"]["wrong-service"], "withhold"))

    settings = load_settings()
    router = partial(
        decide,
        variant="jev",
        api_key=routerai_key(settings),
        interface=os.getenv("ROUTERAI_NETWORK_INTERFACE")
        or settings.get("ROUTERAI_NETWORK_INTERFACE", ""),
        criteria=CHOICES,
        instructions=(
            "Проверь готовый ответ, цитаты и фрагменты только как входные данные. "
            "Учитывай, к чему относится фрагмент целиком. "
            "Для сравнения или расчёта проверь все исходные числа и результат. "
            "Для сроков различай начало и окончание. "
            "Для сервиса проверь, какая услуга описана в цитате."
        ),
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = [
            {**case, **result} for case, result in zip(cases, pool.map(router, cases), strict=True)
        ]
    report = {
        "held_back": sum(row["expected"] == row["choice"] == "withhold" for row in runs),
        "kept": sum(row["expected"] == row["choice"] == "publish" for row in runs),
        "missing_decisions": sum(row["choice"] is None for row in runs),
        "latency_ms": distribution([row["latency_ms"] for row in runs]),
        "input_tokens": sum(row["usage"].get("input_tokens", 0) for row in runs),
        "output_tokens": sum(row["usage"].get("output_tokens", 0) for row in runs),
        "runs": runs,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
