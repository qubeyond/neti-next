"""Test additional retrieval from other queries' snippets with SQLite FTS5."""

import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from evals.compare_search import score, url_key
from evals.experiment_rerank import fts_query
from evals.metrics import retrieval_metrics


def evaluate(runs: list[dict], *, include_results: bool = False) -> dict:
    corpus = defaultdict(set)
    for run in runs:
        for item in run["search"]["results"][:5]:
            corpus[item["url"], item["title"], item["snippet"]].add(run["id"])
    orders = {"web_5": [], "web_3_local_2": [], "web_5_local_2": []}
    elapsed = []
    with closing(sqlite3.connect(":memory:")) as connection:
        start = perf_counter_ns()
        connection.execute("CREATE VIRTUAL TABLE snippets USING fts5(title, text)")
        snippets = list(corpus)
        connection.executemany(
            "INSERT INTO snippets(rowid, title, text) VALUES (?, ?, ?)",
            [(i, title, value) for i, (_, title, value) in enumerate(snippets, 1)],
        )
        connection.commit()
        build_ms = (perf_counter_ns() - start) / 1_000_000
        index_bytes = len(connection.serialize())
        for run in runs:
            search = run["search"]
            if search["status"] not in {"ok", "empty"}:
                continue
            web = search["results"][:5]
            start = perf_counter_ns()
            query = fts_query(search["query"])
            excluded = {url_key(row["url"]) for row in web}
            local = []
            if query:
                matches = connection.execute(
                    "SELECT rowid FROM snippets WHERE snippets MATCH ? "
                    "ORDER BY bm25(snippets, 2.0, 1.0), rowid",
                    (query,),
                )
                for (rowid,) in matches:
                    snippet = snippets[rowid - 1]
                    url, title, value = snippet
                    key = url_key(url)
                    if not key or key in excluded or not corpus[snippet] - {run["id"]}:
                        continue
                    local.append({"url": url, "title": title, "snippet": value})
                    excluded.add(key)
                    if len(local) == 2:
                        break
            elapsed.append((perf_counter_ns() - start) / 1_000_000)
            fixed = [*web[:3], *local, *web[3:]][:5]
            for name, results in zip(orders, (web, fixed, [*web, *local]), strict=True):
                orders[name].append(
                    score(
                        run["id"],
                        list(search["gold_urls"]),
                        {**search, "results": results},
                    )
                )

    complete = {
        name: {
            case["id"] for case in cases if case["gold_urls"] and all(case["gold_urls"].values())
        }
        for name, cases in orders.items()
    }
    control = complete["web_5"]
    return {
        "indexed_snippets": len(corpus),
        "index_bytes": index_bytes,
        "build_ms": build_ms,
        "local_p50_ms": median(elapsed),
        "local_p95_ms": sorted(elapsed)[(len(elapsed) * 95 + 99) // 100 - 1],
        **({"candidate_sets": orders} if include_results else {}),
        "variants": {
            name: {
                "metrics": retrieval_metrics(cases, 7 if name == "web_5_local_2" else 5),
                "new_complete": sorted(complete[name] - control),
                "lost_complete": sorted(control - complete[name]),
            }
            for name, cases in orders.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    print(json.dumps(evaluate(baseline["runs"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
