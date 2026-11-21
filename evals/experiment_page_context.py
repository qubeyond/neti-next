"""Парная проверка фрагмента страницы и поискового сниппета."""

import argparse
import json
import math
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter, sleep

from httpx2 import HTTPError, HTTPStatusError

from agent.config import load_settings, positive_int, required
from agent.domain.evidence import _plain
from agent.domain.passages import QUERY_STOP_WORDS as stop
from agent.providers.http import send_json
from agent.providers.search_apis import _post
from evals.compare_search import EVAL_DIR, read_jsonl
from evals.experiment_rerank import UNIVERSITIES
from evals.metrics import case_outcome
from evals.run_web_baseline import answer_case

DEFAULT_CASES = (
    "sgups-dorm-difference",
    "sgups-dorm-address",
    "sgups-dorm-total",
    "sgups-block-capacity",
    "nspu-masters-dorm",
    "nspu-ege",
    "nspu-triple-price-gap",
    "nstu-business-practice-years",
    "nspu-master-docs",
    "sgugit-dorm-photo",
)
INSTITUTION = re.compile(r"\b(?:" + "|".join(UNIVERSITIES.values()) + r")\b", re.IGNORECASE)


def scrape(url: str, api_key: str) -> dict:
    request = _post(
        "https://api.firecrawl.dev/v2/scrape",
        {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": 25000,
        },
        {"Authorization": f"Bearer {api_key}"},
    )
    started = perf_counter()
    try:
        payload = send_json(request, 30)
        markdown = payload["data"].get("markdown") or ""
        result = {"status": "ok" if markdown else "empty", "markdown": markdown}
    except HTTPStatusError as error:
        result = {"status": f"http_{error.response.status_code}"}
    except (HTTPError, OSError, ValueError, KeyError):
        result = {"status": "error"}
    return {**result, "latency_ms": round((perf_counter() - started) * 1000)}


def words(text):
    return re.findall(r"[а-яёa-z0-9]+", text.lower())


def clean_markdown(markdown: str) -> str:
    markdown = re.sub(r"!\[[^\]]*\]\([^\n]*?\)", "", markdown)
    return re.sub(r"\[([^\]\n]+)\]\(https?://[^\n]+?\)", r"\1", markdown)


def passage(markdown: str, question: str, budget: int) -> str | None:
    markdown = clean_markdown(markdown)
    query = {word[:5] for word in words(question) if len(word) >= 4 and word not in stop}
    lines = [line[: budget // 2] for line in markdown.splitlines() if line.strip()]
    candidates = []
    for start in range(len(lines)):
        chosen = []
        length = 0
        for line in lines[start:]:
            if length + len(line) + 1 > budget:
                break
            chosen.append(line)
            length += len(line) + 1
        if chosen:
            text = "\n".join(chosen)
            matches = {word[:5] for word in words(text)} & query
            candidates.append((text, matches))
    if not candidates:
        return None
    frequency = Counter(term for _, matches in candidates for term in matches)

    return max(
        candidates,
        key=lambda item: sum(math.log1p(len(candidates) / frequency[term]) for term in item[1]),
    )[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, help="JSON с замороженной выдачей")
    parser.add_argument("output", type=Path, help="Новый файл с сырыми результатами")
    pages = parser.add_mutually_exclusive_group()
    pages.add_argument(
        "--cached-pages",
        action="append",
        type=Path,
        default=[],
        help=(
            "Отчёт с сохранёнными страницами. Сравнить замену и добавление без запросов Firecrawl."
        ),
    )
    pages.add_argument(
        "--reuse-pages",
        type=Path,
        help="Повторить сравнение сниппета и страницы по сохранённым результатам чтения.",
    )
    parser.add_argument("--focus-passage", action="store_true")
    parser.add_argument(
        "--case", action="append", dest="cases", help="ID вопроса, повторяемый флаг"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        required=True,
        help="Подтвердить живые модельные вызовы и, без --cached-pages, чтение Firecrawl",
    )
    args = parser.parse_args()
    if args.focus_passage and not args.reuse_pages:
        parser.error("--focus-passage требует --reuse-pages.")
    case_ids = tuple(args.cases) if args.cases else DEFAULT_CASES
    if not 1 <= len(case_ids) <= 10 or len(set(case_ids)) != len(case_ids):
        parser.error("Укажи от 1 до 10 разных кейсов.")
    if args.output.exists():
        parser.error("Выходной файл уже существует.")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    settings = load_settings()
    cached_paths = [args.reuse_pages] if args.reuse_pages else args.cached_pages
    api_key = "" if cached_paths else required(settings, "FIRECRAWL_API_KEY")
    budget = positive_int(settings, "WEB_SOURCE_MAX_CHARS")

    questions = {
        row["id"]: row
        for filename in ("questions.jsonl", "questions-extension.jsonl")
        for row in read_jsonl(EVAL_DIR / filename)
    }
    gold = {
        row["id"]: row
        for filename in ("answers.jsonl", "answers-extension.jsonl")
        for row in read_jsonl(EVAL_DIR / "gold" / filename)
    }
    cache = {
        url: page
        for path in cached_paths
        for url, page in json.loads(path.read_text(encoding="utf-8"))["pages"].items()
    }
    pairs = {}
    runs = {row["id"]: row for row in baseline["runs"]}
    if any(key not in runs or not runs[key]["search"]["results"] for key in case_ids):
        parser.error("Для каждого кейса нужна непустая сохранённая выдача.")
    if cached_paths and any(
        not cache.get(runs[key]["search"]["results"][0]["url"], {}).get("markdown")
        for key in case_ids
    ):
        parser.error("Для каждого кейса нужна сохранённая страница с текстом.")
    with ThreadPoolExecutor(max_workers=3) as pool:
        for case_id in case_ids:
            item = questions[case_id]
            search = runs[case_id]["search"]
            top = search["results"][0]
            url = top["url"]
            if url not in cache:
                cache[url] = scrape(url, api_key)
                sleep(1.2)
            page = cache[url]
            text = passage(page.get("markdown", ""), item["input"]["question"], budget)
            updated = (
                [{**top, "snippet": text}, *search["results"][1:]] if text else search["results"]
            )
            control_search = search
            if args.focus_passage and text:
                focused = INSTITUTION.sub(" ", item["input"]["question"])
                focused_text = passage(page["markdown"], focused, budget) or text
                control_search = {**search, "results": updated}
                updated = [{**top, "snippet": focused_text}, *search["results"][1:]]
            elif args.cached_pages and text:
                original = _plain(top["snippet"])
                length = min(budget, len(_plain(text)))
                replacement = _plain(text)[:length]
                combined = f"{original} {replacement}".strip()[:length]
                control_search = {
                    **search,
                    "results": [
                        {**top, "snippet": replacement},
                        *search["results"][1:],
                    ],
                }
                updated = [{**top, "snippet": combined}, *search["results"][1:]]
            control = pool.submit(
                answer_case,
                item,
                gold[case_id],
                control_search,
                True,
                judge_max_tokens=3072,
            )
            treatment = pool.submit(
                answer_case,
                item,
                gold[case_id],
                {**search, "results": updated},
                True,
                judge_max_tokens=3072,
            )
            pairs[case_id] = (control, treatment, page["status"], bool(text))
            print(f"{case_id} scrape={page['status']}", file=sys.stderr, flush=True)
        results = [
            {
                "id": key,
                "control": control.result(),
                "treatment": treatment.result(),
                "scrape_status": status,
                "changed": changed,
            }
            for key, (control, treatment, status, changed) in pairs.items()
        ]
    report = {
        "mode": (
            "original_vs_focused_passage"
            if args.focus_passage
            else "replace_vs_append_cached"
            if args.cached_pages
            else "snippet_vs_page"
        ),
        "case_ids": case_ids,
        "pages": cache,
        "pairs": results,
        "outcomes": {
            arm: Counter(case_outcome(item[arm]) for item in results)
            for arm in ("control", "treatment")
        },
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["outcomes"], ensure_ascii=False))


if __name__ == "__main__":
    main()
