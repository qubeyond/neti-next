"""Compare Firecrawl search with and without official university domain filters."""

import argparse
import json
from collections import Counter
from pathlib import Path
from time import perf_counter, sleep
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from agent.modules.answers.infrastructure.search_apis import _post, _results
from agent.shared.config import _local_settings, _positive_int, _setting
from agent.shared.errors import AgentError
from evals.compare_search import score, url_key
from evals.metrics import retrieval_metrics
from evals.run_web_baseline import distribution

DOMAINS = {
    "nsu": ("nsu.ru", "education.nsu.ru", "help.nsu.ru", "e-lib.nsu.ru"),
    "nstu": ("nstu.ru", "business.nstu.ru", "ciu.nstu.ru"),
    "nspu": ("nspu.ru", "kf.nspu.ru"),
    "sgugit": ("sgugit.ru", "education.sgugit.ru"),
    "sgups": ("stu.ru", "www.stu.ru"),
    "sib": ("sibsutis.ru", "do.sibsutis.ru", "ndo.sibsutis.ru"),
}
CASES = (
    "nsu-certificate",
    "nstu-budget",
    "nspu-overall-dorm",
    "sgugit-dorm-rule22",
    "sgugit-dorm-photo",
    "sib-archived-profile",
    "sgups-career-audience",
    "sgups-career-vacancies",
)


def search(
    query: str,
    domains: tuple[str, ...],
    key: str,
    timeout: int,
    *,
    with_markdown: bool = False,
) -> dict:
    payload = {"query": query, "limit": 5, "sources": [{"type": "web"}]}
    if domains:
        payload["includeDomains"] = domains
    if with_markdown:
        payload["scrapeOptions"] = {
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": 12000,
        }
    request = _post(
        "https://api.firecrawl.dev/v2/search",
        payload,
        {"Authorization": f"Bearer {key}"},
    )
    started = perf_counter()
    results = []
    error = None
    markdown = {}
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        if with_markdown:
            items = data.get("data", {})
            if isinstance(items, list):
                data["data"] = {"web": items}
            else:
                items = items.get("web", [])
            markdown = {
                item["url"]: item["markdown"]
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and isinstance(item.get("markdown"), str)
                and item["markdown"].strip()
            }
        results = _results("firecrawl", data, 5)
        status = "ok" if results else "empty"
    except HTTPError as failure:
        error = failure.code
        status = "blocked" if error in {401, 402, 403, 429} else "error"
    except (
        AgentError,
        URLError,
        OSError,
        ValueError,
        AttributeError,
        TypeError,
    ) as failure:
        error = type(failure).__name__
        status = "error"
    return {
        "query": query,
        "status": status,
        "results": results,
        "latency_ms": round(1000 * (perf_counter() - started)),
        "error": error,
        **({"markdown": markdown} if with_markdown else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Выходной файл уже существует.")
    config = _local_settings()
    key = _setting(config, "FIRECRAWL_API_KEY")
    timeout = _positive_int(config, "SEARCH_TIMEOUT_SECONDS")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    runs = {row["id"]: row["search"] for row in baseline["runs"]}
    variants = {"web": [], "official_only": []}
    official_matches = Counter()
    total_urls = Counter()
    requests = 0
    for index, case_id in enumerate(CASES):
        source = runs[case_id]
        domains = DOMAINS[case_id.split("-", 1)[0]]
        order = ("web", "official_only") if index % 2 == 0 else ("official_only", "web")
        for name in order:
            if requests:
                sleep(7)
            result = search(
                source["query"],
                domains if name == "official_only" else (),
                key,
                timeout,
            )
            requests += 1
            variants[name].append(score(case_id, list(source["gold_urls"]), result))
            for link in result["results"]:
                total_urls[name] += 1
                if (parsed := url_key(link["url"])) and any(
                    parsed[0] == domain or parsed[0].endswith("." + domain) for domain in domains
                ):
                    official_matches[name] += 1
        if any(rows[-1]["status"] == "blocked" for rows in variants.values()):
            break

    paired = {
        name: {row["id"] for row in rows if row["gold_urls"] and all(row["gold_urls"].values())}
        for name, rows in variants.items()
    }
    report = {
        "requests": requests,
        "summary": {
            name: {
                "statuses": dict(Counter(row["status"] for row in rows)),
                "retrieval": retrieval_metrics(rows, 5),
                "latency_ms": distribution([row["latency_ms"] for row in rows]),
                "official_domain_links": official_matches[name],
                "all_links": total_urls[name],
            }
            for name, rows in variants.items()
        },
        "new_complete": sorted(paired["official_only"] - paired["web"]),
        "lost_complete": sorted(paired["web"] - paired["official_only"]),
        "runs": variants,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
