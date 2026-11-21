"""Строит Markdown для двух парных e2e-прогонов."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from evals.compare_search import EVAL_DIR, read_jsonl
from evals.experiment_evidence_stages import evidence_stages
from evals.metrics import case_outcome, retrieval_metrics
from evals.render_metrics import MISSING, fraction, latency, quality, run_metrics, table

USAGE_FIELDS = {
    "Запросы": "requests",
    "Списания вернулось": "responses_with_cost",
    "Входные токены": "prompt_tokens",
    "Выходные токены": "completion_tokens",
    "Всего токенов": "total_tokens",
    "Стоимость, ₽": "reported_cost_rub",
}


def paired_rows(first: dict, second: dict) -> list[tuple]:
    before = first["runs"]
    after = second["runs"]
    pairs = list(zip(before, after, strict=True))
    ids = [row["id"] for row in before]
    if len(set(ids)) != len(ids) or any(
        (a["id"], a["search"]["query"]) != (b["id"], b["search"]["query"]) for a, b in pairs
    ):
        raise ValueError("Отчёты должны содержать одинаковые вопросы в одном порядке.")
    matches = Counter(
        (case_outcome(a) == "strict_pass", case_outcome(b) == "strict_pass") for a, b in pairs
    )
    return [
        (label, fraction(matches[key], len(pairs)))
        for key, label in (
            ((True, True), "Оба"),
            ((True, False), f"Только {first['search']['provider']}"),
            ((False, True), f"Только {second['search']['provider']}"),
            ((False, False), "Без зачёта"),
        )
    ]


def render(
    first: dict,
    second: dict,
    *,
    exa_rate: Decimal | None,
    answer_tokens: int,
    source_chars: int,
) -> str:
    arms = (first, second)
    if (
        any(arm["mode"] != "e2e" or not arm["llm_judge"] for arm in arms)
        or any(first[key] != second[key] for key in ("rubric", "judge_max_tokens_override"))
        or first["search"]["limit"] != second["search"]["limit"]
    ):
        raise ValueError("Для сравнения нужны два e2e-отчёта с одной рубрикой и лимитами.")
    pairs = paired_rows(first, second)
    names = tuple(arm["search"]["provider"] for arm in arms)
    limit = first["search"]["limit"]
    questions = {}
    keys = {}
    evidence = {}
    for suffix in ("", "-extension"):
        questions.update((r["id"], r) for r in read_jsonl(EVAL_DIR / f"questions{suffix}.jsonl"))
        keys.update(
            (r["id"], r["evidence_ids"])
            for r in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
        evidence.update(json.loads((EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text()))

    def exact_coverage(arm: dict) -> str:
        rows = [
            r
            for case in arm["runs"]
            for r in evidence_stages(case, [(key, evidence[key]) for key in keys[case["id"]]])
        ]
        return fraction(sum(row["context_match"] for row in rows), len(rows))

    def compare(metrics: list[dict]) -> str:
        labels = dict.fromkeys(key for metric in metrics for key in metric)
        return table(
            ("Метрика", *names),
            [(label, *(metric.get(label, MISSING) for metric in metrics)) for label in labels],
        )

    def operations(arm: dict) -> dict:
        search = arm["search"]
        cost = arm["routerai"]["reported_cost_rub"]
        successful = sum(search["statuses"].get(key, 0) for key in ("ok", "empty"))
        successful_live = successful - search["reused_cases"]
        return {
            **run_metrics(arm),
            "Оценка кредитов Firecrawl": search["estimated_credits_successful_requests"],
            "Оценка стоимости Exa, $": (
                f"{exa_rate * successful_live / 1000:.3f}"
                if search["provider"] == "exa" and exa_rate is not None
                else MISSING
            ),
            "Поиск на вопрос, p50/p95, с": latency(search["latency_ms"]),
            "Ответ на вопрос, p50/p95, с": latency(arm["answer_latency_ms"]),
            "Весь прогон с оценкой и паузами, с": f"{arm['wall_latency_ms'] / 1000:.3f}",
            "RouterAI, всего запросов": arm["routerai"]["requests"],
            "RouterAI, всего ₽": f"{Decimal(cost):.4f}" if cost is not None else MISSING,
        }

    def usage_by_stage(arm: dict, stage: str) -> dict:
        call = arm[stage]
        result = {
            label: call[key] if call[key] is not None else MISSING
            for label, key in USAGE_FIELDS.items()
        }
        return {**result, "p50/p95, с": latency(call["latency_ms"])}

    def slices(field: str) -> str:
        def labels(case: dict) -> list[str]:
            question = questions[case["id"]]
            if field != "institutions":
                return [question["domain"]]
            names = question["institutions"]
            return list(dict.fromkeys([*names, *(name.split(" (", 1)[0] for name in names)]))

        groups = sorted({label for row in first["runs"] for label in labels(row)})
        rows = []
        for name in groups:
            stats = []
            for arm in arms:
                cases = [r for r in arm["runs"] if name in labels(r)]
                found = retrieval_metrics([r["search"] for r in cases], limit)
                stats.extend(
                    (
                        fraction(found["hits"], found["eligible"]),
                        fraction(
                            sum(case_outcome(r) == "strict_pass" for r in cases),
                            len(cases),
                        ),
                    )
                )
            rows.append((name, *stats))
        return table(
            (
                "Срез",
                *(
                    f"{name} · {metric}"
                    for name in names
                    for metric in (f"Hit@{limit}", "строгий зачёт")
                ),
            ),
            rows,
        )

    def outcomes(arm: dict) -> dict:
        return dict(Counter(case_outcome(r) for r in arm["runs"]))

    all_outcomes = sorted({o for arm in arms for o in outcomes(arm)})
    sections = [
        "# Eval · Firecrawl и Exa Auto",
        "## Условия",
        table(
            ("Параметр", "Значение"),
            [
                ("Набор", f"expanded · {len(first['runs'])} веб-вопросов"),
                ("Лимит выдачи", limit),
                ("Рубрика", first["rubric"]),
                ("Модель генерации", ", ".join(first["generation"]["models"])),
                ("Модель оценщика", ", ".join(first["judge"]["models"])),
                ("Лимит оценщика", first["judge_max_tokens_override"]),
                (
                    "Срезы по вузам",
                    "Основной вуз включает филиалы и подразделения, межвузовские вопросы "
                    "входят в несколько срезов",
                ),
                (
                    "Контекст / вывод ответа",
                    f"{source_chars} символов на источник / {answer_tokens} токенов",
                ),
                (
                    "Поиск",
                    " · ".join(
                        f"{name} — {arm['search']['requests']} новых, "
                        f"{arm['search']['reused_cases']} сохранённых"
                        for name, arm in zip(names, arms, strict=True)
                    ),
                ),
                (
                    "Тарифы",
                    "[Exa](https://exa.ai/pricing) · [Firecrawl](https://docs.firecrawl.dev/features/search#cost-implications)",
                ),
            ],
        ),
        "## Выполнение и затраты",
        compare(
            [
                {key: val if val is not None else MISSING for key, val in operations(arm).items()}
                for arm in arms
            ]
        ),
        "## Качество",
        compare(
            [
                {
                    "Точные эталонные выдержки в контексте": exact_coverage(arm),
                    **quality(arm),
                }
                for arm in arms
            ]
        ),
        "## Исходы",
        compare([{key: outcomes(arm).get(key, 0) for key in all_outcomes} for arm in arms]),
        "## Парный строгий зачёт",
        table(("Ветка", "Кейсы"), pairs),
        "## По вузам",
        slices("institutions"),
        "## По темам",
        slices("domain"),
        "## Генерация",
        compare([usage_by_stage(arm, "generation") for arm in arms]),
        "## Оценщик",
        compare([usage_by_stage(arm, "judge") for arm in arms]),
    ]
    return "\n\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--exa-usd-per-1000", type=Decimal)
    parser.add_argument("--answer-tokens", type=int, required=True)
    parser.add_argument("--source-chars", type=int, required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in (args.first, args.second)]
    print(
        render(
            *reports,
            exa_rate=args.exa_usd_per_1000,
            answer_tokens=args.answer_tokens,
            source_chars=args.source_chars,
        ),
        end="",
    )


if __name__ == "__main__":
    main()
