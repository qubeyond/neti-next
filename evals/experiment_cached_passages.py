"""Проверяет выбор отрывка из страницы, ранее прочитанной для другого вопроса."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter_ns

from agent.modules.answers.domain.evidence import _plain, prepare_sources
from evals.compare_search import EVAL_DIR, read_jsonl, url_key
from evals.experiment_evidence_stages import evidence_stages
from evals.experiment_page_context import passage
from evals.run_web_baseline import distribution

LIMIT = 1500


def archive(paths: list[Path]) -> dict:
    pages = defaultdict(dict)
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if "pairs" in report:
            for pair in report["pairs"]:
                case = pair["control"]
                url = case["search"]["results"][0]["url"]
                text = report["pages"][url].get("markdown")
                if text:
                    pages[url_key(url)][case["id"]] = text
        else:
            text = report["page"].get("markdown")
            if text:
                case_id = report["runs"]["control"]["id"]
                pages[url_key(report["target"])][case_id] = text
    return pages


def compare(
    case: dict,
    pages: dict,
    refs: list[tuple[str, dict]],
    *,
    cached_limit: int,
    preserve_snippets: bool,
) -> dict | None:
    run = case["search"]
    used = []
    results = []
    started = perf_counter_ns()
    for source in run["results"][:5]:
        owners = pages.get(url_key(source["url"]), {})
        if not owners or case["id"] in owners:
            results.append(source)
            continue
        text = next(iter(owners.values()))
        if excerpt := passage(text, run["query"], cached_limit):
            if preserve_snippets:
                original = _plain(source["snippet"])
                excerpt = _plain(excerpt)
                if excerpt in original:
                    results.append(source)
                    continue
                excerpt = f"{original} ... {excerpt}" if original else excerpt
            used.append(source["url"])
            results.append({**source, "snippet": excerpt})
        else:
            results.append(source)
    elapsed = (perf_counter_ns() - started) // 1000
    if not used:
        return None
    updated = {**run, "results": results}
    contexts = {
        "control": prepare_sources(run["results"][:5], LIMIT),
        "cached": prepare_sources(results, LIMIT),
    }
    return {
        "id": case["id"],
        "domain": case["domain"],
        "latency_us": elapsed,
        "reused_urls": used,
        "context_chars": {
            arm: sum(len(source["text"]) for source in context) for arm, context in contexts.items()
        },
        "evidence": {
            arm: [
                hit["evidence"]
                for hit in evidence_stages({**case, "sources": context}, refs)
                if hit["context_match"]
            ]
            for arm, context in contexts.items()
        },
        "control": run,
        "cached": updated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("pages", type=Path, nargs="+")
    parser.add_argument("--cached-limit", type=int, default=LIMIT)
    parser.add_argument("--preserve-snippets", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.cached_limit <= LIMIT:
        parser.error(f"--cached-limit должен быть в диапазоне от 1 до {LIMIT}.")
    pages = archive(args.pages)
    gold = {}
    evidence = {}
    for suffix in ("", "-extension"):
        gold.update(
            (case["id"], case["evidence_ids"])
            for case in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
        evidence.update(
            json.loads((EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text(encoding="utf-8"))
        )
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    rows = [
        result
        for case in baseline["runs"]
        if (
            result := compare(
                case,
                pages,
                [(ref, evidence[ref]) for ref in gold[case["id"]]],
                cached_limit=args.cached_limit,
                preserve_snippets=args.preserve_snippets,
            )
        )
    ]
    summary = {
        "cached_limit": args.cached_limit,
        "preserve_snippets": args.preserve_snippets,
        "eligible": len(rows),
        "domains": dict(Counter(row["domain"] for row in rows)),
        "evidence": {
            arm: sum(len(row["evidence"][arm]) for row in rows) for arm in ("control", "cached")
        },
        "context_chars": {
            arm: sum(row["context_chars"][arm] for row in rows) for arm in ("control", "cached")
        },
        "wins": [
            row["id"]
            for row in rows
            if set(row["evidence"]["cached"]) - set(row["evidence"]["control"])
        ],
        "losses": [
            row["id"]
            for row in rows
            if set(row["evidence"]["control"]) - set(row["evidence"]["cached"])
        ],
        "latency_us": distribution([row["latency_us"] for row in rows]),
    }
    print(json.dumps({"summary": summary, "runs": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
