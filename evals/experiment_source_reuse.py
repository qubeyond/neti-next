"""Estimate known-URL coverage of a corpus built from other questions."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from evals.compare_search import url_key


def evaluate(baseline: dict, page_reports: list[dict]) -> dict:
    url_owners = defaultdict(set)
    page_owners = defaultdict(set)
    page_bytes = {}
    for case in baseline["runs"]:
        for result in case["search"]["results"][:5]:
            if key := url_key(result["url"]):
                url_owners[key].add(case["id"])
    for report in page_reports:
        for pair in report["pairs"]:
            url = pair["control"]["search"]["results"][0]["url"]
            page = report["pages"][url]
            if (key := url_key(url)) and page.get("markdown"):
                page_owners[key].add(pair["id"])
                page_bytes[key] = len(page["markdown"].encode())

    domains = defaultdict(Counter)
    recovered = {"snapshots": [], "pages": []}
    for case in baseline["runs"]:
        case_id = case["id"]
        gold = case["search"]["gold_urls"]
        if not gold:
            continue
        counts = domains[case["domain"]]
        counts["eligible"] += 1
        found = {url_key(url) for url, rank in gold.items() if rank and rank <= 5}
        missing = {url_key(url) for url in gold} - found
        if not missing:
            counts["web_complete"] += 1
        for name, owners in (("snapshots", url_owners), ("pages", page_owners)):
            available = {key for key in missing if owners.get(key, set()) - {case_id}}
            if available:
                counts[f"{name}_adds_any_missing"] += 1
            if missing and missing <= available:
                recovered[name].append(case_id)
                counts[f"{name}_adds_all_missing"] += 1

    totals = sum(domains.values(), Counter())
    return {
        "unique_result_urls": len(url_owners),
        "result_urls_in_multiple_questions": sum(len(ids) > 1 for ids in url_owners.values()),
        "successfully_read_urls": len(page_bytes),
        "markdown_bytes": sum(page_bytes.values()),
        "overall": dict(totals),
        "by_domain": {domain: dict(counts) for domain, counts in domains.items()},
        "fully_recovered_ids": recovered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("pages", type=Path, nargs="+")
    args = parser.parse_args()
    result = evaluate(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        [json.loads(path.read_text(encoding="utf-8")) for path in args.pages],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
