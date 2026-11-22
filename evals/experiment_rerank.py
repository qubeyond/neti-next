"""Compare lexical reranking on frozen search snapshots without network requests."""

import argparse
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from evals.compare_search import score, url_key
from evals.experiment_domain_search import DOMAINS
from evals.metrics import retrieval_metrics

UNIVERSITIES = {
    "nsu": r"нгу|новосибирский государственный университет",
    "nstu": r"нгту|нэти|новосибирский государственный технический университет",
    "nspu": r"нгпу|новосибирский государственный педагогический университет",
    "sgugit": r"сгугит|сибирский государственный университет геосистем и технологий",
    "sgups": r"сгупс|сибирский государственный университет путей сообщения",
    "sib": r"сибгути|сибгутиc|сибирский государственный университет телекоммуникаций и информатики",
}

STOP_WORDS = frozenset(
    [
        "в",
        "во",
        "на",
        "не",
        "по",
        "для",
        "от",
        "до",
        "или",
        "как",
        "какой",
        "какая",
        "какие",
        "какое",
        "где",
        "когда",
        "что",
        "ли",
        "кто",
        "это",
        "мне",
        "мой",
        "моя",
        "я",
        "мы",
        "если",
        "нужно",
        "можно",
        "узнать",
        "сколько",
    ]
)


def fts_query(query: str) -> str:
    tokens = set(re.findall(r"[^\W_]+", query.lower()))
    terms = {
        f'"{word[:4]}"*' if len(word) >= 5 and word.isalpha() else f'"{word}"'
        for word in tokens - STOP_WORDS
        if len(word) >= 2 or word.isdigit()
    }
    return " OR ".join(sorted(terms))


def rerank(connection: sqlite3.Connection, query: str, results: list[dict]) -> list[int]:
    expression = fts_query(query)
    original = list(range(1, len(results) + 1))
    if not expression or not results:
        return original
    connection.execute("DELETE FROM candidates")
    connection.executemany(
        "INSERT INTO candidates(rowid, title, snippet) VALUES (?, ?, ?)",
        [(rank, row["title"], row["snippet"]) for rank, row in enumerate(results, 1)],
    )
    matched = [
        rowid
        for (rowid,) in connection.execute(
            "SELECT rowid FROM candidates WHERE candidates MATCH ? "
            "ORDER BY bm25(candidates, 2.0, 1.0), rowid",
            (expression,),
        )
    ]
    return matched + [rank for rank in original if rank not in matched]


def official_order(
    query: str, results: list[dict], *, prefer_year: bool = False
) -> tuple[list[int], bool]:
    domains = {
        domain.removeprefix("www.")
        for institution, names in UNIVERSITIES.items()
        if re.search(rf"\b(?:{names})\b", query, re.IGNORECASE)
        for domain in DOMAINS[institution]
    }

    def official(index: int) -> bool:
        parsed = url_key(results[index - 1]["url"])
        return bool(
            parsed
            and any(parsed[0] == domain or parsed[0].endswith("." + domain) for domain in domains)
        )

    years = set(re.findall(r"(?<!\d)20\d{2}(?!\d)", query)) if prefer_year else set()
    year = next(iter(years)) if len(years) == 1 else None

    def key(index: int) -> tuple[bool, bool]:
        source = results[index - 1]
        has_year = bool(
            year and re.search(rf"(?<!\d){year}(?!\d)", f"{source['title']} {source['url']}")
        )
        return not official(index), not has_year

    order = sorted(range(1, len(results) + 1), key=key)
    return order, bool(domains)


def evaluate(
    runs: list[dict], *, prioritize_official: bool = False, prefer_year: bool = False
) -> dict:
    orders = {name: [] for name in ("firecrawl", "bm25", "rank_fusion")}
    official_summary = None
    if prioritize_official or prefer_year:
        orders["official"] = []
        official_summary = {"recognized": 0, "reordered": 0}
    if prefer_year:
        orders["official_year"] = []
    durations = []
    by_domain = {}
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE VIRTUAL TABLE candidates USING fts5(title, snippet)")
        for run in runs:
            search = run["search"]
            if search["status"] not in {"ok", "empty"}:
                continue
            results = search["results"][:5]
            original = list(range(1, len(results) + 1))
            start = perf_counter_ns()
            lexical = rerank(connection, search["query"], results)
            fused = sorted(
                original,
                key=lambda rank: -(1 / (60 + rank) + 1 / (61 + lexical.index(rank))),
            )
            durations.append((perf_counter_ns() - start) / 1_000_000)
            by_domain.setdefault(run["domain"], []).append(run["id"])
            rankings = [original, lexical, fused]
            if official_summary is not None:
                official, recognized = official_order(search["query"], results)
                official_summary["recognized"] += recognized
                official_summary["reordered"] += official != original
                rankings.append(official)
            if prefer_year:
                rankings.append(official_order(search["query"], results, prefer_year=True)[0])
            for name, ranks in zip(orders, rankings, strict=True):
                orders[name].append(
                    score(
                        run["id"],
                        list(search["gold_urls"]),
                        {**search, "results": [results[rank - 1] for rank in ranks]},
                    )
                )
    if not durations:
        raise ValueError("The snapshot has no successful search attempts.")

    report = {}
    for name, cases in orders.items():
        wins = []
        losses = []
        for control, variant in zip(orders["firecrawl"], cases, strict=True):
            if (1 in variant["gold_urls"].values()) > (1 in control["gold_urls"].values()):
                wins.append(variant["id"])
            if (1 in variant["gold_urls"].values()) < (1 in control["gold_urls"].values()):
                losses.append(variant["id"])
        report[name] = {
            "metrics": {k: retrieval_metrics(cases, k) for k in (1, 3, 5)},
            "hit_at_1_wins": wins,
            "hit_at_1_losses": losses,
            "hit_at_1_by_domain": {
                domain: retrieval_metrics([row for row in cases if row["id"] in ids], 1)
                for domain, ids in by_domain.items()
            },
        }
    return {
        "runs": len(runs),
        "sqlite_version": sqlite3.sqlite_version,
        "local_latency_ms": {
            "p50": median(durations),
            "p95": sorted(durations)[(len(durations) * 95 + 99) // 100 - 1],
        },
        "variants": report,
        **({"official_summary": official_summary} if official_summary is not None else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--year", action="store_true")
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    print(
        json.dumps(
            evaluate(
                baseline["runs"],
                prioritize_official=args.official,
                prefer_year=args.year,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
