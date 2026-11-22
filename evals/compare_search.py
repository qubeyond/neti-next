import argparse
import json
import re
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.parse import parse_qsl, urlsplit

from agent.config import load_settings, positive_int, required
from agent.domain.errors import AgentError
from agent.providers.search import (
    PROVIDERS,
    SearchBlocked,
    make_searcher,
)
from agent.providers.search_apis import (
    ALTERNATIVES,
    ATTRIBUTION,
    missing_setting,
)

EVAL_DIR = Path(__file__).resolve().parent
PILOT_IDS = {
    "nsu-certificate",
    "vki-deadline",
    "nstu-transfer",
    "sgups-dorm-difference",
    "nspu-card-price",
    "nspu-branch-amend",
    "sgugit-reader-card",
    "sib-access",
    "sib-distance",
    "dorm-nspu-sgugit",
}
QUERY_NOISE = {
    "а",
    "вот",
    "где",
    "если",
    "как",
    "какие",
    "какой",
    "какова",
    "куда",
    "ли",
    "мне",
    "моём",
    "можно",
    "нужно",
    "пожалуйста",
    "правда",
    "скажи",
    "сколько",
    "спрашиваю",
    "там",
    "что",
    "это",
    "я",
}


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def url_key(url: str) -> tuple | None:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            return None
        return (
            parts.hostname.removeprefix("www."),
            re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(), parts.path.rstrip("/")),
            tuple(
                sorted(
                    (name, value)
                    for name, value in parse_qsl(parts.query, keep_blank_values=True)
                    if not name.lower().startswith("utm_")
                )
            ),
        )
    except ValueError:
        return None


def compact_query(query: str) -> str:
    words = re.findall(r"[\w]+(?:[-/][\w]+)*", query)
    return " ".join(word for word in words if word.casefold() not in QUERY_NOISE).strip() or query


def fuse(first: list, second: list, limit: int) -> list:
    scores = {}
    candidates = {}
    for results in (first, second):
        seen = set()
        for rank, result in enumerate(results, start=1):
            key = url_key(result["url"])
            if key is None or key in seen:
                continue
            seen.add(key)
            candidates.setdefault(key, result)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return [candidates[key] for key in sorted(scores, key=scores.get, reverse=True)[:limit]]


def search_once(search, query: str, blocked_reason: str | None) -> dict:
    if blocked_reason:
        return {
            "query": query,
            "status": "skipped",
            "error": blocked_reason,
            "latency_ms": 0,
            "results": [],
        }
    started_at = perf_counter()
    try:
        results = search(query)
        status, error = ("ok" if results else "empty"), None
    except SearchBlocked as failure:
        status, error, results = "blocked", str(failure), []
    except AgentError as failure:
        status, error, results = "error", str(failure), []
    latency = round((perf_counter() - started_at) * 1000)
    return {
        "query": query,
        "status": status,
        "error": error,
        "latency_ms": latency,
        "results": results,
    }


def score(case_id: str, expected: list[str], run: dict) -> dict:
    ranked = {}
    for rank, result in enumerate(run["results"], start=1):
        ranked.setdefault(url_key(result["url"]), rank)
    return {
        "id": case_id,
        **run,
        "gold_urls": {url: ranked.get(url_key(url)) for url in expected},
    }


def summarize(runs: dict) -> dict:
    summary = {}
    for provider, cases in runs.items():
        usable = [item for item in cases if item["status"] in {"ok", "empty"}]
        summary[provider] = {
            "cases": len(cases),
            "comparable_cases": len(usable),
            "any_gold_url_found": sum(any(item["gold_urls"].values()) for item in usable)
            if usable
            else None,
            "all_gold_urls_found": sum(all(item["gold_urls"].values()) for item in usable)
            if usable
            else None,
            "empty": sum(item["status"] == "empty" for item in cases),
            "errors": sum(item["status"] == "error" for item in cases),
            "blocked": sum(item["status"] == "blocked" for item in cases),
            "skipped": sum(item["status"] == "skipped" for item in cases),
            "median_latency_ms": median(item["latency_ms"] for item in usable) if usable else None,
        }
    return summary


def compare(
    searchers: dict,
    use_all: bool,
    enhanced: bool = False,
    limit: int = 5,
    *,
    blocked: dict | None = None,
) -> dict:
    questions = [
        item
        for item in read_jsonl(EVAL_DIR / "questions.jsonl")
        if item["suite"] == "web" and (use_all or item["id"] in PILOT_IDS)
    ]
    evidence = json.loads((EVAL_DIR / "gold/evidence.json").read_text(encoding="utf-8"))
    gold = {item["id"]: item for item in read_jsonl(EVAL_DIR / "gold/answers.jsonl")}
    runs = {provider: [] for provider in searchers}
    enhanced_runs = {provider: [] for provider in searchers}
    if blocked is None:
        blocked = {}
    for item in questions:
        case_id = item["id"]
        query = item["input"]["question"]
        expected = list(
            dict.fromkeys(
                evidence[evidence_id]["url"] for evidence_id in gold[case_id]["evidence_ids"]
            )
        )
        for provider, search in searchers.items():
            first = search_once(search, query, blocked.get(provider))
            if first["status"] == "blocked":
                blocked[provider] = first["error"]
            runs[provider].append(score(case_id, expected, first))
            if enhanced:
                augmented = first
                second_query = compact_query(query)
                if provider not in blocked and second_query != query:
                    second = search_once(search, second_query, None)
                    if second["status"] == "blocked":
                        blocked[provider] = second["error"]
                    results = fuse(first["results"], second["results"], limit)
                    status = (
                        "ok"
                        if results
                        else next(
                            (run["status"] for run in (second, first) if run["status"] != "empty"),
                            "empty",
                        )
                    )
                    augmented = {
                        **first,
                        "status": status,
                        "results": results,
                        "latency_ms": first["latency_ms"] + second["latency_ms"],
                        "error": second["error"] or first["error"],
                        "retry_query": second_query,
                        "retry_status": second["status"],
                    }
                enhanced_runs[provider].append(score(case_id, expected, augmented))
    report = {"summary": summarize(runs), "runs": runs}
    if enhanced:
        report["enhanced"] = {
            "summary": summarize(enhanced_runs),
            "runs": enhanced_runs,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Сравнить поисковики без вызова модели")
    parser.add_argument("--all", action="store_true", help="Все веб-кейсы вместо десяти пилотных")
    parser.add_argument(
        "--enhanced",
        action="store_true",
        help="Также проверить второй запрос с объединением выдачи",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--provider",
        action="append",
        choices=PROVIDERS,
        help="Выбрать поисковик; можно повторить",
    )
    selection.add_argument(
        "--alternatives",
        action="store_true",
        help="Все доступные варианты без Tavily и DuckDuckGo",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Количество прогонов (1–5)")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 5:
        parser.error("--repeat должен быть от 1 до 5")
    try:
        settings = load_settings()
        providers = (
            ALTERNATIVES
            if args.alternatives
            else args.provider or (required(settings, "SEARCH_PROVIDER"),)
        )
        missing = {}
        if args.alternatives:
            missing = {
                provider: name
                for provider in providers
                if (name := missing_setting(provider, settings))
            }
            providers = [provider for provider in providers if provider not in missing]
        searchers = {provider: make_searcher(provider) for provider in providers}
        limit = positive_int(settings, "SEARCH_MAX_RESULTS")
    except AgentError as error:
        parser.exit(1, f"Ошибка: {error}\n")
    blocked = {}
    reports = [
        compare(searchers, args.all, args.enhanced, limit, blocked=blocked)
        for _ in range(args.repeat)
    ]
    report = reports[0] if args.repeat == 1 else {"repeats": reports}
    if args.alternatives:
        report["not_configured"] = missing
    attribution = {
        provider: ATTRIBUTION[provider] for provider in searchers if provider in ATTRIBUTION
    }
    if attribution:
        report["attribution"] = attribution
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
