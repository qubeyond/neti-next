"""Измеряет потери доказательств при уменьшении длины фрагмента."""

import argparse
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.modules.answers.domain.evidence import _citation_text, _plain
from agent.modules.answers.domain.passages import (
    QUERY_STOP_WORDS,
    WORD,
    prefix_with_evidence,
)
from evals.compare_search import EVAL_DIR, read_jsonl, url_key
from evals.experiment_passage_coverage import quote_form
from evals.experiment_rerank import official_order
from evals.metrics import case_outcome
from evals.run_web_baseline import answer_case, usage_summary

LIMITS = (500, 750, 1000, 1500)
SENTENCE_END = re.compile(r"[.!?](?=\s+[А-ЯЁA-Z]|\s*$)")
HEADING = re.compile(r"(?<!\S)#{1,6}\s+\S")
COOKIE_MARKERS = re.compile(r"\bcookies?\b|\bкуки\b|яндекс[. ]?метрик", re.IGNORECASE)


def drop_cookie_preamble(text: str) -> str:
    first_heading = HEADING.search(text)
    if first_heading and len(COOKIE_MARKERS.findall(text[: first_heading.start()])) >= 2:
        return text[first_heading.start() :]
    return text


def relevant_window(text: str, question: str, limit: int, *, sentence_edges: bool = False) -> str:
    if len(text) <= limit:
        return text
    query = {
        word[:5]
        for word in WORD.findall(question.lower())
        if len(word) >= 4 and word not in QUERY_STOP_WORDS
    }
    if not query:
        return text[:limit]
    positions = [
        (token.start(), token.end(), token[0].lower()[:5]) for token in WORD.finditer(text)
    ]
    frequency = Counter(term for _, _, term in positions if term in query)
    best_score = -1.0
    best_start = 0
    best_end = limit
    for start in (
        0,
        *(start for start, _, _ in positions if start <= len(text) - limit),
    ):
        end = start + limit
        included = {
            term
            for first, last, term in positions
            if first >= start and last <= end and term in frequency
        }
        score = sum(1 / frequency[term] for term in sorted(included))
        if score > best_score:
            best_score, best_start, best_end = score, start, end
    if sentence_edges and not SENTENCE_END.match(text, best_end - 1):
        end_match = SENTENCE_END.search(text, best_end, min(len(text), best_end + 120))
        if end_match:
            best_end = end_match.end()
            best_start = next(
                (start for start, _, _ in positions if start >= best_end - limit),
                best_end - limit,
            )
    return text[best_start:best_end]


def compact_search(
    search: dict,
    *,
    rank_clauses: bool,
    preamble_slack: int = 0,
    preserve_primary: bool = False,
) -> dict:
    results = []
    snippets = [_plain(source["snippet"]) for source in search["results"]]
    nonempty = [
        (index, source) for index, source in enumerate(search["results"]) if snippets[index]
    ]
    order, _ = (
        official_order(search["query"], [source for _, source in nonempty])
        if preserve_primary
        else ([], False)
    )
    primary = nonempty[order[0] - 1][0] if order else None
    for index, source in enumerate(search["results"]):
        text = snippets[index][:1500]
        if index == primary:
            results.append({**source, "snippet": text})
            continue
        limit = 750
        if preamble_slack:
            cleaned = drop_cookie_preamble(text)
            if cleaned != text and limit < len(cleaned) <= limit + preamble_slack:
                limit = len(cleaned)
            text = cleaned
        excerpt = (
            prefix_with_evidence(text, search["query"], limit, rank_clauses=True)
            if rank_clauses
            else text[:limit]
        )
        results.append({**source, "snippet": excerpt})
    return {**search, "results": results}


def compare_generation(
    rows: list[dict],
    keys: dict,
    *,
    rank_clauses: bool = False,
    preamble_slack: int = 0,
    preserve_primary: bool = False,
) -> dict:
    questions = {
        row["id"]: row
        for suffix in ("", "-extension")
        for row in read_jsonl(EVAL_DIR / f"questions{suffix}.jsonl")
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {}
        for row in rows:
            case_id = row["id"]
            search = row["search"]
            compact = compact_search(
                search,
                rank_clauses=rank_clauses,
                preamble_slack=preamble_slack,
                preserve_primary=preserve_primary,
            )
            control = (
                compact_search(search, rank_clauses=rank_clauses) if preamble_slack else search
            )
            for variant, context in (("control", control), ("compact", compact)):
                jobs[case_id, variant] = pool.submit(
                    answer_case,
                    questions[case_id],
                    keys[case_id],
                    context,
                    True,
                    judge_max_tokens=3072,
                )
        runs = {
            arm: [jobs[row["id"], arm].result() for row in rows] for arm in ("control", "compact")
        }
    return {
        "control_mode": "compact" if preamble_slack else "full",
        "runs": runs,
        "summary": {
            arm: {
                "outcomes": Counter(map(case_outcome, answers)),
                "generation": usage_summary(answers, "generation"),
                "judge": usage_summary(answers, "judge"),
            }
            for arm, answers in runs.items()
        },
    }


def evaluate(
    rows: list[dict],
    keys: dict,
    evidence: dict,
    *,
    centered: bool = False,
    sentence_edges: bool = False,
    append_evidence: bool = False,
    rank_clauses: bool = False,
    drop_preamble: bool = False,
    preamble_slack: int = 0,
    preserve_primary: bool = False,
) -> dict:
    results = {}
    for limit in LIMITS:
        characters = 0
        matched = set()
        preserved = set()
        for row in rows:
            sources = {}
            query = row["search"]["query"]
            available = row.get("sources", [])
            order, _ = official_order(query, available) if preserve_primary else ([], False)
            primary = available[order[0] - 1]["source"] if order else None
            for source in available:
                text = source["text"]
                source_limit = 1500 if source["source"] == primary else limit
                if drop_preamble:
                    compact = drop_cookie_preamble(text)
                    if compact != text and limit < len(compact) <= limit + preamble_slack:
                        source_limit = len(compact)
                    text = compact
                if append_evidence:
                    excerpt = prefix_with_evidence(
                        text, query, source_limit, rank_clauses=rank_clauses
                    )
                elif centered:
                    excerpt = relevant_window(text, query, limit, sentence_edges=sentence_edges)
                else:
                    excerpt = text[:source_limit]
                sources[source["source"]] = (url_key(source["url"]), excerpt)
            characters += sum(len(text) for _, text in sources.values())
            for index, citation in enumerate(row.get("citations", [])):
                _, text = sources.get(citation["source"], (None, ""))
                quote = _citation_text(citation["quote"])
                if quote and quote in _citation_text(text):
                    preserved.add(f"{row['id']}:{index}")
            for ref in keys[row["id"]]["evidence_ids"]:
                item = evidence[ref]
                quote = " " + quote_form(item["quote"])
                if any(
                    url == url_key(item["url"]) and quote in " " + quote_form(text)
                    for url, text in sources.values()
                ):
                    matched.add(f"{row['id']}:{ref}")
        results[limit] = {
            "characters": characters,
            "gold": sorted(matched),
            "verified": sorted(preserved),
        }
    control = results[1500]
    for row in results.values():
        row["lost_gold"] = sorted(set(control["gold"]) - set(row["gold"]))
        row["lost_verified"] = sorted(set(control["verified"]) - set(row["verified"]))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("combined", type=Path)
    parser.add_argument("--live-reading-report", type=Path)
    parser.add_argument("--live-holdout-report", type=Path)
    parser.add_argument("--centered-report", type=Path, help="Офлайн-срез E42.")
    parser.add_argument("--sentence-edges", action="store_true")
    parser.add_argument("--append-evidence", action="store_true")
    parser.add_argument("--rank-clauses", action="store_true")
    parser.add_argument("--centered-arm", default="control")
    parser.add_argument("--drop-preamble", action="store_true")
    parser.add_argument("--preamble-slack", type=int, choices=(0, 50), default=0)
    parser.add_argument("--preserve-primary", action="store_true")
    parser.add_argument("--live-primary-reports", type=Path, nargs=3, metavar="REPORT")
    args = parser.parse_args()
    keys = {}
    evidence = {}
    for suffix in ("", "-extension"):
        keys.update(
            (row["id"], row) for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
        evidence.update(
            json.loads((EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text(encoding="utf-8"))
        )
    sets = {
        "baseline": json.loads(args.baseline.read_text(encoding="utf-8"))["runs"],
        **json.loads(args.combined.read_text(encoding="utf-8"))["runs"],
    }
    if args.live_primary_reports:
        rows = []
        selections = (
            {"nspu-ege"},
            {"sgugit-reader-card"},
            {"nsu-certificate", "vki-deadline", "sgups-career-audience"},
        )
        for path, ids in zip(args.live_primary_reports, selections, strict=True):
            report = json.loads(path.read_text(encoding="utf-8"))
            if "pairs" in report:
                selected = [pair["treatment"] for pair in report["pairs"] if pair["id"] in ids]
            else:
                selected = [row for row in report["answer_runs"]["markdown"] if row["id"] in ids]
            if len(selected) != len(ids):
                parser.error(f"Неполный срез в {path.name}.")
            rows.extend(selected)
        print(
            json.dumps(
                compare_generation(rows, keys, preserve_primary=True),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.live_reading_report:
        reading = json.loads(args.live_reading_report.read_text(encoding="utf-8"))
        additional = {
            "nstu-business-practice-years",
            "nspu-triple-price-gap",
            "sgugit-dorm-photo",
        }
        rows = [
            *sets["combined"],
            *(row for row in reading["answer_runs"]["markdown"] if row["id"] in additional),
        ]
        print(
            json.dumps(
                compare_generation(rows, keys, rank_clauses=args.rank_clauses),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.live_holdout_report:
        reading = json.loads(args.live_holdout_report.read_text(encoding="utf-8"))
        selected = {
            "nsu-library-login",
            "vki-deadline",
            "nstu-app",
            "sgups-career-audience",
        }
        if args.preamble_slack:
            selected = {"nsu-library-login", "sgups-career-audience"}
        rows = [pair["treatment"] for pair in reading["pairs"] if pair["id"] in selected]
        if len(rows) != len(selected):
            parser.error("В отчёте отсутствует часть контрольного среза.")
        print(
            json.dumps(
                compare_generation(
                    rows, keys, rank_clauses=True, preamble_slack=args.preamble_slack
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.centered_report:
        report = json.loads(args.centered_report.read_text(encoding="utf-8"))
        rows = report["runs"][args.centered_arm]
        evaluated = {
            "prefix": evaluate(rows, keys, evidence),
            "centered": evaluate(rows, keys, evidence, centered=True),
        }
        if args.sentence_edges:
            evaluated["sentence_edges"] = evaluate(
                rows, keys, evidence, centered=True, sentence_edges=True
            )
        if args.append_evidence:
            evaluated["prefix_with_evidence"] = evaluate(rows, keys, evidence, append_evidence=True)
        if args.rank_clauses:
            evaluated["ranked_clauses"] = evaluate(
                rows, keys, evidence, append_evidence=True, rank_clauses=True
            )
        if args.drop_preamble:
            evaluated["without_preamble"] = evaluate(
                rows,
                keys,
                evidence,
                append_evidence=True,
                rank_clauses=True,
                drop_preamble=True,
            )
        if args.preamble_slack:
            evaluated["preamble_slack"] = evaluate(
                rows,
                keys,
                evidence,
                append_evidence=True,
                rank_clauses=True,
                drop_preamble=True,
                preamble_slack=args.preamble_slack,
            )
        if args.preserve_primary:
            evaluated["primary_full"] = evaluate(rows, keys, evidence, preserve_primary=True)
        print(
            json.dumps(
                evaluated,
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(
        json.dumps(
            {name: evaluate(rows, keys, evidence) for name, rows in sets.items()},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
