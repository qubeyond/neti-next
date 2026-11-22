"""Compare passage extraction and table extraction on the same frozen pages."""

import argparse
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter_ns

from agent.domain.evidence import _plain
from evals.compare_search import EVAL_DIR, read_jsonl, url_key
from evals.experiment_page_context import clean_markdown, passage, stop, words
from evals.metrics import case_outcome
from evals.run_web_baseline import answer_case, distribution, usage_summary

CASES = (
    "sgups-dorm-difference",
    "sgups-dorm-address",
    "sgups-dorm-total",
    "sgups-block-capacity",
    "nspu-master-docs",
    "nspu-master-consent",
)
BUDGET = 1500


def table_rows(markdown: str) -> list[tuple[str, list[str], list[list[str]]]]:
    tables = []
    heading = ""
    table_heading = ""
    header = []
    rows = []
    for line in [*clean_markdown(markdown).splitlines(), ""]:
        if not line.strip().startswith("|"):
            if header and rows:
                tables.append((table_heading, header, rows))
            header, rows = [], []
            if match := re.match(r"^\s*#{1,6}\s+(.+)", line):
                heading = _plain(match[1])
            continue
        cells = [
            _plain(re.sub(r"<br\s*/?>", " ", cell, flags=re.IGNORECASE)).replace("**", "")
            for cell in line.strip().strip("|").split("|")
        ]
        if not any(cells) or all(re.fullmatch(r"[: -]*", cell) for cell in cells):
            continue
        if len(cells) >= 2 and (
            cells[0] == "No" or all(not re.search(r"\d", cell) for cell in cells)
        ):
            if header and rows:
                tables.append((table_heading, header, rows))
            header, rows = cells, []
            table_heading = heading
        elif header:
            if len(cells) == len(header) - 1 and header[0] == "No":
                cells.insert(0, "")
            if len(cells) == len(header):
                rows.append(cells)
    return tables


def table_context(markdown: str, question: str, *, with_heading: bool = False) -> str:
    query = {word[:5] for word in words(question) if word not in stop and len(word) >= 4}
    numbers = set(re.findall(r"\d+(?:/\d+)?", question))

    def overlap(cells: list[str]) -> int:
        return len(query & {word[:5] for cell in cells for word in words(cell)})

    candidates = table_rows(markdown)
    if not candidates:
        return ""
    heading, header, rows = max(
        candidates,
        key=lambda item: 2 * overlap(item[1]) + max(overlap(row) for row in item[2]),
    )
    ordered = sorted(
        enumerate(rows),
        key=lambda row: (
            -(
                overlap(row[1])
                + 3 * len(numbers & set(re.findall(r"\d+(?:/\d+)?", " ".join(row[1][:2]))))
            )
        ),
    )
    prefix = " | ".join(header)
    selected = []
    size = len(prefix)
    for index, row in ordered:
        text = " | ".join(row)
        if size + len(text) + 3 <= BUDGET:
            size += len(text) + 3
            selected.append((index, text))
    body = " / ".join([prefix, *(text for _, text in sorted(selected))]) if selected else ""
    return f"{heading}. {body}" if with_heading and heading and body else body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("reading", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--scope",
        action="store_true",
        help="Сравнить таблицу с заголовком раздела и без него.",
    )
    args = parser.parse_args()
    records = json.loads(args.baseline.read_text(encoding="utf-8"))["runs"]
    by_id = {row["id"]: row for row in records}
    cache = {
        url_key(url): page["markdown"]
        for url, page in json.loads(args.reading.read_text(encoding="utf-8"))["pages"].items()
        if page.get("markdown")
    }
    questions, gold = (
        {row["id"]: row for filename in filenames for row in read_jsonl(EVAL_DIR / filename)}
        for filenames in (
            ("questions.jsonl", "questions-extension.jsonl"),
            ("gold/answers.jsonl", "gold/answers-extension.jsonl"),
        )
    )
    inputs = {}
    durations = []
    cases = CASES[:4] if args.scope else CASES
    control_name = "table" if args.scope else "passage"
    treatment_name = "scoped_table" if args.scope else "table"
    for case_id in cases:
        search = by_id[case_id]["search"]
        for index, source in enumerate(search["results"]):
            markdown = cache.get(url_key(source["url"]), "")
            started = perf_counter_ns()
            table = table_context(markdown, search["query"])
            if not table:
                continue
            durations.append(round((perf_counter_ns() - started) / 1000))
            control = table if args.scope else passage(markdown, search["query"], BUDGET)
            treatment = (
                table_context(markdown, search["query"], with_heading=True) if args.scope else table
            )
            if args.scope and (treatment == table or len(_plain(treatment)) > BUDGET):
                parser.error("Для сравнения нужен заголовок и та же таблица в пределах лимита.")
            inputs[case_id] = {
                "source_url": source["url"],
                "rank": index + 1,
                "arms": {
                    name: {
                        **search,
                        "results": [
                            *search["results"][:index],
                            {**source, "snippet": context},
                            *search["results"][index + 1 :],
                        ],
                    }
                    for name, context in (
                        (control_name, control),
                        (treatment_name, treatment),
                    )
                },
            }
            break
    if len(inputs) != len(cases):
        parser.error("Для части кейсов недоступна сохранённая таблица.")
    report = {"inputs": inputs, "extraction_latency_us": distribution(durations)}
    if args.live:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {
                (case_id, name): pool.submit(
                    answer_case,
                    questions[case_id],
                    gold[case_id],
                    search,
                    True,
                    judge_max_tokens=3072,
                )
                for case_id, item in inputs.items()
                for name, search in item["arms"].items()
            }
            arms = {
                name: [jobs[case_id, name].result() for case_id in cases]
                for name in (control_name, treatment_name)
            }
        report["runs"] = arms
        report["summary"] = {
            name: {
                "outcomes": Counter(map(case_outcome, rows)),
                "generation": usage_summary(rows, "generation"),
                "judge": usage_summary(rows, "judge"),
            }
            for name, rows in arms.items()
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
