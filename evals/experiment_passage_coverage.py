"""Measure recoverable evidence quotes with one or two document spans."""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from textwrap import wrap
from time import perf_counter_ns

from agent.domain.evidence import _plain
from evals.compare_search import EVAL_DIR, read_jsonl, url_key
from evals.experiment_page_context import clean_markdown, passage, stop, words
from evals.run_web_baseline import distribution

BUDGET = 1500


def two_passages(markdown: str, question: str) -> str:
    limit = BUDGET // 2 - 1
    lines = [
        line for raw in clean_markdown(markdown).splitlines() for line in wrap(raw, width=limit)
    ]
    query = {word[:5] for word in words(question) if len(word) >= 4 and word not in stop}
    spans = []
    for start in range(len(lines)):
        end = start
        length = 0
        while end < len(lines) and length + len(lines[end]) + 1 <= limit + 1:
            length += len(lines[end]) + 1
            end += 1
        text = "\n".join(lines[start:end])
        terms = query & {word[:5] for word in words(text)}
        spans.append((start, end, text, terms))
    if not spans:
        return ""
    frequency = Counter(term for _, _, _, terms in spans for term in terms)

    def weight(terms: set[str]) -> float:
        return sum(math.log1p(len(spans) / frequency[term]) for term in sorted(terms))

    first = max(spans, key=lambda span: weight(span[3]))
    remaining = [span for span in spans if span[1] <= first[0] or span[0] >= first[1]]
    if not remaining:
        return first[2]
    second = max(remaining, key=lambda span: (weight(span[3] - first[3]), weight(span[3])))
    return "\n".join(span[2] for span in sorted((first, second)))


def quote_form(text: str) -> str:
    return " ".join(words(_plain(text).replace("ё", "е"))) + " "


def section_passage(markdown: str, question: str) -> str:
    segments = re.split(r"(?m)^\s*#{1,6}\s+([^\n]+)\n", clean_markdown(markdown))
    sections = [("", segments[0]), *zip(segments[1::2], segments[2::2], strict=True)]
    if len(sections) == 1:
        return passage(markdown, question, BUDGET) or ""
    query = {word[:5] for word in words(question) if len(word) >= 4 and word not in stop}
    candidates = []
    for header, body in sections:
        header = _plain(header)[:250]
        if excerpt := passage(body, question, BUDGET - len(header) - 2):
            text = f"{header}. {excerpt}" if header else excerpt
            matches = query & {word[:5] for word in words(text)}
            candidates.append((text, matches))
    frequency = Counter(term for _, matches in candidates for term in matches)
    return max(
        candidates,
        key=lambda item: sum(
            math.log1p(len(candidates) / frequency[term]) for term in sorted(item[1])
        ),
        default=("", set()),
    )[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reading_reports", type=Path, nargs="+")
    parser.add_argument("--sections", action="store_true")
    args = parser.parse_args()
    gold = {}
    evidence = {}
    for suffix in ("", "-extension"):
        gold.update(
            (row["id"], row["evidence_ids"])
            for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
        evidence.update(json.loads((EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text()))
    rows = []
    selectors = [
        ("one", lambda text, query: passage(text, query, BUDGET) or ""),
        ("two", two_passages),
    ]
    if args.sections:
        selectors.append(("section", section_passage))
    latency = {name: [] for name, _ in selectors}
    for path in args.reading_reports:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for pair in payload["pairs"]:
            case_id = pair["id"]
            search = pair["control"]["search"]
            url = search["results"][0]["url"]
            raw = payload["pages"][url].get("markdown")
            refs = [key for key in gold[case_id] if url_key(evidence[key]["url"]) == url_key(url)]
            if not raw or not refs:
                continue
            fragments = {"page": quote_form(clean_markdown(raw))}
            for name, select in selectors:
                started = perf_counter_ns()
                fragments[name] = quote_form(_plain(select(raw, search["query"]))[:BUDGET])
                latency[name].append((perf_counter_ns() - started) // 1000)
            for ref in refs:
                quote = " " + quote_form(evidence[ref]["quote"])
                rows.append(
                    {
                        "id": case_id,
                        "ref": ref,
                        **{key: quote in " " + text for key, text in fragments.items()},
                    }
                )
    report = {
        "quote_instances": len(rows),
        "case_count": len({row["id"] for row in rows}),
        "matches": {name: sum(row[name] for row in rows) for name in ("page", *latency)},
        "latency_microseconds": {name: distribution(samples) for name, samples in latency.items()},
        "rows": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
