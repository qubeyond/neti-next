"""Формирование сопоставимых таблиц из исходных результатов eval."""

import argparse
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from evals.metrics import case_outcome, retrieval_metrics
from evals.run_web_baseline import distribution

MODES = ("e2e", "retrieval", "oracle", "grounding")
MISSING = "—"
OUTCOMES = (
    ("strict_pass", "Строгий зачёт"),
    ("unverified", "Нет подтверждения"),
    ("invalid", "Невалидный ответ агента"),
    ("judge_failed", "Нет валидной оценки ответа"),
    ("incorrect_or_incomplete", "Неверные или неполные факты"),
    ("critical_error", "Критичная ошибка"),
    ("correct_but_unsupported", "Факты без достаточных доказательств"),
    (
        "correct_but_marked_partial",
        "Факты и доказательства верны, ответ помечен частичным",
    ),
    ("invalid_or_missing_citation", "Нет валидных цитат"),
    ("not_graded", "Ответ без вызова оценщика"),
    ("generation_error", "Сбой генерации"),
    ("search_unavailable", "Поиск недоступен"),
)
ANSWER_METRICS = (
    "Context Fact Coverage",
    "Complete Context Rate",
    "Answer Coverage",
    "Judge Coverage (среди полных и частичных ответов)",
    "Conditional Answer Accuracy",
    "Fact Recall (в оценённых ответах)",
    "Fact Support Rate (среди верных фактов)",
    "Answer Support Rate (в оценённых ответах)",
    "Citation Verbatim Rate",
    "Critical Error Rate (в оценённых ответах)",
    "Strict Case Pass Rate",
    "Abstention Pass Rate",
)


def table(headers, rows) -> str:
    return "\n".join(
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in (headers, ("---",) * len(headers), *rows)
    )


def fraction(count: int, total: int) -> str:
    return f"{count}/{total} ({count / total:.1%})" if total else MISSING


def abstained(item: dict) -> bool:
    return (
        item["status"] == "unverified"
        and not item.get("citations")
        and not item.get("malformed_citations")
    )


def quality(report: dict) -> dict[str, str]:
    rows = report["runs"]
    limit = report["search"]["limit"]
    searches = [item["search"] for item in rows]
    result = {}
    ranking = retrieval_metrics(searches, limit)
    eligible = ranking["eligible"]
    if eligible:
        for label, key in (
            (f"Hit Rate@{limit}", "hits"),
            (f"Complete Retrieval Rate@{limit}", "complete_hits"),
        ):
            result[label] = fraction(ranking[key], eligible)
        for label, key in (("Recall", "recall"), ("MRR", "mrr"), ("nDCG", "ndcg")):
            result[f"{label}@{limit}"] = f"{ranking[f'{key}@{limit}']:.4f}"
    if report["mode"] == "retrieval":
        return result
    answered = [item for item in rows if item["status"] in {"answered", "partial"}]
    result["Answer Coverage"] = fraction(len(answered), len(rows))
    citations = [citation for item in rows for citation in item.get("citations", [])]
    malformed = sum(item.get("malformed_citations", 0) for item in rows)
    result["Citation Verbatim Rate"] = fraction(
        sum(citation["verified"] for citation in citations), len(citations) + malformed
    )
    if report["mode"] == "grounding":
        passed = sum(abstained(item) for item in rows)
        result["Abstention Pass Rate"] = fraction(passed, len(rows))
    if not report["llm_judge"]:
        return result

    grades = [item["grade"] for item in answered if "correct" in item.get("grade", {})]
    facts = [fact for grade in grades for fact in grade.get("facts", [])]
    correct_facts = [fact for fact in facts if fact["verdict"] == "correct"]
    context = [item["grade"]["facts"] for item in rows if item.get("grade", {}).get("facts")]
    context_facts = [fact for item in context for fact in item]
    result.update(
        {
            "Context Fact Coverage": fraction(
                sum(fact["in_context"] for fact in context_facts), len(context_facts)
            ),
            "Complete Context Rate": fraction(
                sum(all(fact["in_context"] for fact in item) for item in context),
                len(context),
            ),
            "Judge Coverage (среди полных и частичных ответов)": fraction(
                len(grades), len(answered)
            ),
            "Conditional Answer Accuracy": fraction(
                sum(grade["correct"] for grade in grades), len(grades)
            ),
            "Fact Recall (в оценённых ответах)": fraction(len(correct_facts), len(facts)),
            "Fact Support Rate (среди верных фактов)": fraction(
                sum(fact["supported"] for fact in correct_facts), len(correct_facts)
            ),
            "Answer Support Rate (в оценённых ответах)": fraction(
                sum(grade["supported"] for grade in grades), len(grades)
            ),
            "Critical Error Rate (в оценённых ответах)": fraction(
                sum(grade["critical_error"] for grade in grades), len(grades)
            ),
            "Strict Case Pass Rate": fraction(
                sum(case_outcome(item) == "strict_pass" for item in rows), len(rows)
            ),
        }
    )
    return result


def usage(report: dict, stage: str) -> tuple:
    calls = [item.get(stage) for item in report["runs"] if item.get(f"{stage}_attempted")]
    entries = [call["usage"] for call in calls if call and call.get("usage")]
    cost = [Decimal(str(entry["cost"])) for entry in entries if entry.get("cost") is not None]
    totals = []
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        numbers = [entry[key] for entry in entries if type(entry.get(key)) is int]
        totals.append(sum(numbers) if numbers else MISSING)
    stats = distribution([call["latency_ms"] for call in calls if call])
    return (
        len(calls),
        len(entries),
        len(cost),
        *totals,
        format(sum(cost).normalize(), "f") if cost else MISSING,
        seconds(stats["median"]),
        seconds(stats["p95"]),
    )


def seconds(value: float | None) -> str:
    return f"{value / 1000:.3f}" if value is not None else MISSING


def latency(values: dict) -> str:
    if not values["samples"]:
        return MISSING
    return f"{seconds(values['median'])} / {seconds(values['p95'])}"


def run_metrics(report: dict) -> dict[str, str | int]:
    rows = report["runs"]
    search = report["search"]
    provider = search["provider"]
    statuses = search["statuses"]
    truncated = report["generation"]["finish_reasons"].get("length", 0)
    valid_grades = sum("correct" in row.get("grade", {}) for row in rows)
    grade_attempts = sum(row["judge_attempted"] for row in rows)
    return {
        "Кейсов": len(rows),
        "Новых вызовов поиска": search["requests"],
        "Кейсов с сохранённой выдачей": search["reused_cases"],
        "Успешный поиск": statuses.get("ok", 0) if provider else MISSING,
        "Пустая выдача": statuses.get("empty", 0) if provider else MISSING,
        "Блокировки поиска": statuses.get("blocked", 0) if provider else MISSING,
        "Ошибки поиска": statuses.get("error", 0) if provider else MISSING,
        "Кейсы без запроса после блокировки": statuses.get("skipped", 0) if provider else MISSING,
        "Обрезано ответов модели": truncated if report["mode"] != "retrieval" else MISSING,
        "Валидных оценок / попыток": fraction(valid_grades, grade_attempts),
    }


def mode_table(names, values: dict) -> str:
    return table(
        ("Метрика", *MODES),
        [(name, *(values.get(mode, {}).get(name, MISSING) for mode in MODES)) for name in names],
    )


def robustness_table(report: dict) -> str:
    kinds = report["by_kind"]
    channels = report["attacks_by_channel"]
    repeated = report["repeat_stability"]
    rows = [
        ("Кейсов с живым замером", len({row["id"] for row in report["runs"]})),
        (
            "Уточнения без поддержки интерфейса",
            len(report["clarification_unsupported"]),
        ),
        (
            "Согласованность статуса между повторами",
            fraction(repeated["same_status"], repeated["eligible_cases"]),
        ),
        (
            "Согласованность исхода между повторами",
            fraction(repeated["same_evaluated_outcome"], repeated["eligible_cases"]),
        ),
    ]
    probe_outcomes = Counter(row["outcome"] for row in report["runs"] if row["variant"] == "probe")
    for outcome, label in (
        ("invalid", "Невалидные ответы на пробах"),
        ("generation_error", "Сбои генерации на пробах"),
        ("judge_failed", "Сбои оценщика на пробах"),
    ):
        rows.append((label, probe_outcomes[outcome]))
    for kind, label in (
        (
            "paraphrase",
            "Paraphrase Utility Retention (сохранение зачёта после перефраза)",
        ),
        (
            "prompt-injection",
            "Injection Utility Retention (сохранение зачёта при инъекции)",
        ),
    ):
        if row := kinds.get(kind):
            rows.append((label, fraction(row["retained_strict_pass"], row["clean_strict_pass"])))
    for kind, outcome, label in (
        (
            "false-premise",
            "strict_pass",
            "False Premise Correction Rate (исправление предпосылки)",
        ),
        ("scope-trap", "safe_abstention", "Safe Abstention Rate (строгий отказ)"),
        ("scope-trap", "false_answer", "Unsafe Answer Rate (ложный ответ на ловушку)"),
    ):
        if row := kinds.get(kind):
            rows.append((label, fraction(row["outcomes"].get(outcome, 0), row["attempts"])))
    if (scope := kinds.get("scope-trap")) and "semantic_abstentions" in scope:
        rows.append(
            (
                "Semantic Abstention Rate (корректный отказ по смыслу)",
                fraction(scope["semantic_abstentions"], scope["attempts"]),
            )
        )
    for channel, data in sorted(channels.items()):
        rows.append(
            (
                f"Attack Marker Rate · {channel}",
                fraction(data["marker_seen"], data["trials_with_output"]),
            )
        )
    return table(("Метрика", "Значение"), rows)


def render(
    reports: dict,
    version: str,
    measured_on: date,
    dataset: str,
    answer_tokens: int,
    *,
    robustness: dict | None = None,
) -> str:
    e2e = reports["e2e"]
    limit = e2e["search"]["limit"]
    modes = {mode: run_metrics(report) for mode, report in reports.items()}
    measured = {mode: quality(report) for mode, report in reports.items()}
    limits = sorted(
        {report["search"]["limit"] for report in reports.values() if report["search"]["provider"]}
    )
    ranking_labels = ("Hit Rate", "Recall", "Complete Retrieval Rate", "MRR", "nDCG")
    names = [f"{label}@{k}" for k in limits for label in ranking_labels]
    stage_names = {"generation": "Генерация", "judge": "Оценщик"}
    models = {
        stage: ", ".join(
            sorted({name for report in reports.values() for name in report[stage]["models"]})
        )
        or MISSING
        for stage in stage_names
    }
    configuration = (
        ("Дата", measured_on),
        ("Выборка", dataset),
        ("Поиск e2e", f"{e2e['search']['provider']} · top-{limit}"),
        ("Интервал поиска e2e, с", e2e["search"]["min_request_interval_s"]),
        ("Модель ответа", models["generation"]),
        ("Оценщик", models["judge"]),
        ("Рубрика e2e", e2e["rubric"] or MISSING),
        ("Лимит токенов ответа", answer_tokens),
        (
            "Лимит токенов оценщика e2e",
            e2e["judge_max_tokens_override"] or answer_tokens,
        ),
    )
    outcomes = {
        mode: Counter(case_outcome(item) for item in reports[mode]["runs"])
        for mode in ("e2e", "oracle")
        if mode in reports
    }
    parts = [f"# Eval · {version}"]

    def add(title: str, content: str) -> None:
        parts.extend((f"## {title}", content))

    add("Конфигурация", table(("Параметр", "Значение"), configuration))
    add("Охват и сбои", mode_table(modes["e2e"], modes))
    add("Качество", mode_table((*names, *ANSWER_METRICS), measured))
    add(
        "Исходы кейсов",
        table(
            ("Исход", "e2e", "oracle"),
            [
                (
                    label,
                    outcomes["e2e"][name],
                    outcomes["oracle"][name] if "oracle" in outcomes else MISSING,
                )
                for name, label in OUTCOMES
            ],
        ),
    )

    oracle = {}
    if e2e["llm_judge"] and reports.get("oracle", {}).get("llm_judge"):
        oracle = {
            row["id"]: case_outcome(row) == "strict_pass" for row in reports["oracle"]["runs"]
        }
        paired = Counter(
            (case_outcome(row) == "strict_pass", oracle[row["id"]])
            for row in e2e["runs"]
            if row["id"] in oracle
        )
        add(
            "Парное сравнение",
            table(
                ("Строгий зачёт", "Кейсов"),
                (
                    ("e2e и oracle", paired[True, True]),
                    ("Только e2e", paired[True, False]),
                    ("Только oracle", paired[False, True]),
                    ("Без зачёта", paired[False, False]),
                ),
            ),
        )

    if e2e["llm_judge"]:
        with_urls, with_facts = [], []
        for row in e2e["runs"]:
            passed = case_outcome(row) == "strict_pass"
            if retrieval_metrics([row["search"]], limit)["complete_hits"]:
                with_urls.append(passed)
            facts = row.get("grade", {}).get("facts", [])
            if facts and all(fact["in_context"] for fact in facts):
                with_facts.append(passed)
        add(
            "Поиск и строгий зачёт",
            table(
                ("Подвыборка e2e", "Кейсов", "Строго зачтено"),
                (
                    (
                        "Все эталонные URL в выдаче",
                        len(with_urls),
                        fraction(sum(with_urls), len(with_urls)),
                    ),
                    (
                        "Все факты в контексте по оценке модели",
                        len(with_facts),
                        fraction(sum(with_facts), len(with_facts)),
                    ),
                ),
            ),
        )

    for field, title in (
        ("domain", "Срезы по доменам"),
        ("skills", "Срезы по навыкам"),
    ):
        group_runs = {}
        for row in e2e["runs"]:
            for group in (row["domain"],) if field == "domain" else row["skills"]:
                group_runs.setdefault(group, []).append(row)
        slices = []
        for group, subset in sorted(group_runs.items()):
            results = quality({**e2e, "runs": subset})
            matched = [oracle[row["id"]] for row in subset if row["id"] in oracle]
            slices.append(
                (
                    group,
                    len(subset),
                    results.get(f"Hit Rate@{limit}", MISSING),
                    results.get("Strict Case Pass Rate", MISSING),
                    fraction(sum(matched), len(matched)),
                )
            )
        headers = (
            "Срез",
            "Кейсов e2e",
            f"Hit Rate@{limit}",
            "Строгий e2e",
            "Строгий oracle",
        )
        add(title, table(headers, slices))

    if "grounding" in reports:
        add(
            "Контрольные отказы",
            table(
                ("Кейс", "Статус", "Зачёт"),
                [
                    (row["id"], row["status"], int(abstained(row)))
                    for row in reports["grounding"]["runs"]
                ],
            ),
        )

    if robustness is not None:
        add("Устойчивость", robustness_table(robustness))

    timings = {
        mode: {
            "Время прогона, с": seconds(report["wall_latency_ms"]),
            "Поиск p50 / p95, с": latency(report["search"]["latency_ms"])
            if report["search"]["provider"]
            else MISSING,
            "Поиск + ответ p50 / p95, с": latency(report["answer_latency_ms"]),
        }
        for mode, report in reports.items()
    }
    add("Задержки", mode_table(timings["e2e"], timings))
    cost_headers = (
        "Режим",
        "Этап",
        "Попыток",
        "С usage",
        "Со стоимостью",
        "Вход",
        "Выход",
        "Всего",
        "usage.cost, ₽",
        "p50, с",
        "p95, с",
    )
    calls = [
        (mode, label, *usage(report, stage))
        for mode, report in reports.items()
        for stage, label in stage_names.items()
        if any(row.get(f"{stage}_attempted") for row in report["runs"])
    ]
    if robustness is not None:
        calls.extend(
            ("robustness", label, *usage(robustness, stage))
            for stage, label in stage_names.items()
            if any(row.get(f"{stage}_attempted") for row in robustness["runs"])
        )
    add("Вызовы RouterAI", table(cost_headers, calls))
    cost_totals = []
    cost_reports = reports if robustness is None else {**reports, "robustness": robustness}
    for mode, report in cost_reports.items():
        costs = [
            Decimal(stage["reported_cost_rub"])
            for stage in (report["generation"], report["judge"])
            if stage["reported_cost_rub"] is not None
        ]
        if costs:
            cost_totals.append((mode, format(sum(costs).normalize(), "f")))
    add(
        "Суммарная стоимость RouterAI",
        table(("Режим", "Сумма usage.cost, ₽"), cost_totals),
    )
    add(
        "Условия подсчёта",
        table(
            ("Показатель", "Условие"),
            (
                ("URL-метрики", "Известные эталоны, успешные и пустые выдачи"),
                ("Оценка фактов и контекста", "Только валидные вердикты оценщика"),
                ("Строгий зачёт", "Все кейсы режима, включая сбои"),
                ("Grounding", "Статус unverified и отсутствие цитат"),
                ("Денежные списания Firecrawl", "Не замерялись"),
                ("Задержка p95", "Эмпирический 95-й перцентиль, nearest rank"),
                (
                    "Поиск + ответ",
                    "Сумма этапов по кейсу без ожидания в очереди и оценки",
                ),
            ),
        ),
    )
    return "\n\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Собрать Markdown-таблицы из результатов eval без API."
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Каталог с e2e.json и опциональными отчётами по остальным режимам",
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--answer-tokens", required=True, type=int)
    parser.add_argument("--robustness", type=Path, help="JSON из run_robustness.")
    args = parser.parse_args()
    try:
        reports = {}
        for mode in MODES:
            path = args.run_dir / f"{mode}.json"
            if mode != "e2e" and not path.exists():
                continue
            report = json.loads(path.read_text(encoding="utf-8"))
            ids = {item["id"] for item in report["runs"]}
            if (
                report["mode"] != mode
                or len(ids) != len(report["runs"])
                or len(ids) != report["cases"]
            ):
                raise ValueError(f"{path.name}: режим, ID или число кейсов не совпадают")
            reports[mode] = report
        print(
            render(
                reports,
                args.version,
                args.date,
                args.dataset,
                args.answer_tokens,
                robustness=json.loads(args.robustness.read_text(encoding="utf-8"))
                if args.robustness
                else None,
            ),
            end="",
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.error(f"Не удалось сформировать отчёт: {error}")


if __name__ == "__main__":
    main()
