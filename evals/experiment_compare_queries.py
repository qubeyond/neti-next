"""Сравнивает прямой поиск, подзапросы и ответы по сохранённым страницам."""

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import chain, zip_longest
from pathlib import Path
from time import sleep

from agent.config import load_settings, positive_int, required
from evals.compare_search import EVAL_DIR, read_jsonl, score, url_key
from evals.experiment_domain_search import search
from evals.experiment_page_context import passage
from evals.metrics import case_outcome, retrieval_metrics
from evals.run_web_baseline import answer_case, usage_summary

SUBQUESTIONS = {
    "dorm-nspu-sgugit": (
        "НГПУ 2026 график заселения первокурсники ИЕСЭН общежитие 1 первый день",
        "СГУГиТ 2026 общий график заселения студентов в общежития начало",
    ),
    "dorm-nsu-sgugit": (
        "НГУ начало заселения первокурсников в общежитие август 2026",
        "СГУГиТ 2026 общий график заселения студентов в общежития начало",
    ),
    "sgups-budget-gap": (
        "СГУПС 2026 бюджет сроки приема документов внутренние вступительные испытания",
        "СГУПС 2026 бюджет сроки приема документов только ЕГЭ",
    ),
}


def merge(results: list[list[dict]]) -> list[dict]:
    unique = {}
    for choices in zip_longest(*results):
        for row in choices:
            if row and (key := url_key(row["url"])):
                unique.setdefault(key, row)
        if len(unique) >= 5:
            break
    return list(unique.values())[:5]


def replay(report: dict) -> dict:
    pairs = {}
    for case_id, pair in report["pairs"].items():
        direct = pair["direct"]
        arms = [
            pair["direct"]["results"],
            *(part["results"] for part in pair["subqueries"]),
        ]
        combined = {}
        for row in chain(
            direct["results"][:3], pair["decomposed"]["results"], direct["results"][3:]
        ):
            if key := url_key(row["url"]):
                combined.setdefault(key, row)
        expected = list(direct["gold_urls"])
        pairs[case_id] = {
            "direct": direct,
            "decomposed": pair["decomposed"],
            "combined": score(
                case_id, expected, {**direct, "results": list(combined.values())[:5]}
            ),
            "candidate_ceiling": len(
                {url_key(row["url"]) for row in chain.from_iterable(arms)}.intersection(
                    map(url_key, expected)
                )
            )
            == len(set(map(url_key, expected))),
        }
    return {
        "network_calls": 0,
        "pairs": pairs,
        "metrics": {
            arm: retrieval_metrics([case[arm] for case in pairs.values()], 5)
            for arm in ("direct", "decomposed", "combined")
        },
    }


def answer_replay(replay_report: dict, reports: list[Path]) -> dict:
    pages = {}
    for path in reports:
        data = json.loads(path.read_text(encoding="utf-8"))
        pages.update(
            (url_key(url), page["markdown"])
            for url, page in data.get("pages", {}).items()
            if page.get("markdown")
        )
        pages.update(
            (url_key(url), markdown)
            for run in data.get("search_runs", [])
            for url, markdown in run.get("markdown", {}).items()
            if markdown
        )

    budget = positive_int(load_settings(), "WEB_SOURCE_MAX_CHARS")
    questions, gold = (
        {row["id"]: row for name in names for row in read_jsonl(EVAL_DIR / name)}
        for names in (
            ("questions.jsonl", "questions-extension.jsonl"),
            ("gold/answers.jsonl", "gold/answers-extension.jsonl"),
        )
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {}
        for case_id, pair in replay_report["pairs"].items():
            for arm in ("direct", "combined"):
                search_result = pair[arm]
                results = []
                for source in search_result["results"]:
                    markdown = pages.get(url_key(source["url"]))
                    snippet = (
                        passage(markdown, search_result["query"], budget) if markdown else None
                    )
                    results.append({**source, "snippet": snippet or source["snippet"]})
                jobs[case_id, arm] = pool.submit(
                    answer_case,
                    questions[case_id],
                    gold[case_id],
                    {**search_result, "results": results},
                    True,
                    judge_max_tokens=3072,
                )
        runs = {
            arm: [jobs[case_id, arm].result() for case_id in replay_report["pairs"]]
            for arm in ("direct", "combined")
        }
    return {
        "network_search_calls": 0,
        "runs": runs,
        "summary": {
            arm: {
                "outcomes": dict(Counter(map(case_outcome, rows))),
                "generation": usage_summary(rows, "generation"),
                "judge": usage_summary(rows, "judge"),
            }
            for arm, rows in runs.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", action="store_true", help="Переиграть сохранённый отчёт без API.")
    mode.add_argument(
        "--answer-replay",
        action="store_true",
        help="Сгенерировать ответы по отчёту replay.",
    )
    parser.add_argument("--reading-report", action="append", type=Path, default=[])
    args = parser.parse_args()
    if args.answer_replay:
        if not args.reading_report:
            parser.error("--answer-replay требует хотя бы один --reading-report.")
        report = answer_replay(
            json.loads(args.baseline.read_text(encoding="utf-8")), args.reading_report
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.replay:
        report = replay(json.loads(args.baseline.read_text(encoding="utf-8")))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    settings = load_settings()
    key = required(settings, "FIRECRAWL_API_KEY")
    timeout = positive_int(settings, "SEARCH_TIMEOUT_SECONDS")
    rows = json.loads(args.baseline.read_text(encoding="utf-8"))["runs"]
    saved = {row["id"]: row["search"] for row in rows}
    calls = {}

    def lookup(query: str) -> dict:
        if query not in calls:
            if calls:
                sleep(7)
            calls[query] = search(query, (), key, timeout)
        return calls[query]

    pairs = {}
    for case_id, subqueries in SUBQUESTIONS.items():
        row = saved[case_id]
        direct = lookup(row["query"])
        parts = [lookup(query) for query in subqueries]
        results = merge([part["results"] for part in parts])
        expected = list(row["gold_urls"])
        pairs[case_id] = {
            "direct": score(case_id, expected, direct),
            "decomposed": score(
                case_id,
                expected,
                {
                    "query": row["query"],
                    "status": "ok" if results else "empty",
                    "results": results,
                    "latency_ms": sum(part["latency_ms"] for part in parts),
                },
            ),
            "subqueries": parts,
        }
    report = {
        "network_calls": len(calls),
        "pairs": pairs,
        "metrics": {
            arm: retrieval_metrics([case[arm] for case in pairs.values()], 5)
            for arm in ("direct", "decomposed")
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
