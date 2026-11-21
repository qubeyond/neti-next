"""Проверяет сверку цитат с источником после нормализации оформления."""

import json

from agent.modules.answers.domain.evidence import parse_answer, prepare_sources
from agent.modules.answers.domain.sources import SearchResult


def test_prepare_sources_preserves_rank_after_skipping_empty_result():
    results: list[SearchResult] = [
        {"url": "https://example.org/empty", "title": "Пустой", "snippet": "<p> </p>"},
        {
            "url": "https://example.org/filled",
            "title": "<b>Правила</b>",
            "snippet": "Срок &nbsp; завершения",
        },
    ]

    assert prepare_sources(results, 4) == [
        {"source": 2, "url": results[1]["url"], "title": "Правила", "text": "Срок"}
    ]


def test_prepare_sources_keeps_the_prefix_and_adds_the_best_matching_clause():
    text = (
        "Памятка студентам. "
        + "Общий порядок. " * 10
        + "Стоимость известна. Стоимость проживания — 1159 рублей."
    )
    result: SearchResult = {"url": "https://example.org/fees", "title": "Памятка", "snippet": text}
    limit = 120

    [prepared] = prepare_sources([result], limit, question="Какая стоимость проживания?")

    assert len(prepared["text"]) <= limit
    assert prepared["text"].startswith("Памятка студентам.")
    assert prepared["text"].endswith(" ... Стоимость проживания — 1159 рублей.")
    assert prepare_sources([result], limit)[0]["text"] == text[:limit]
    assert prepare_sources([result], limit, question="Где библиотека?")[0]["text"] == text[:limit]


def test_citations_match_visible_markdown_without_changing_facts():
    sources = [
        {
            "source": 1,
            "url": "https://example.org/source",
            "text": r"ИЕСЭН \- 24, 25 августа. Заселение **с 26 августа** для студентов.",
        }
    ]
    citations = [
        (1, "ИЕСЭН - 24, 25 августа."),
        (1, "Заселение с 26 августа для студентов."),
        (1, "Заселение с 25 августа для студентов."),
        (1, "Заселение с 26 августа для абитуриентов."),
        (2, "ИЕСЭН - 24, 25 августа."),
    ]
    raw = json.dumps(
        {
            "status": "answered",
            "answer": "Сроки отличаются.",
            "citations": [{"source": source, "quote": quote} for source, quote in citations],
        },
        ensure_ascii=False,
    )
    parsed = parse_answer(raw, sources)

    assert [citation["verified"] for citation in parsed["citations"]] == [
        True,
        True,
        False,
        False,
        False,
    ]


def test_ellipsis_yields_only_ordered_excerpts_from_one_source():
    budget = "Бюджет — 1159 рублей."
    paid = "Платная основа — 1517 рублей."
    combined = f"{budget} … {paid}"
    sources = [
        {
            "source": 1,
            "url": "https://example.org/fees",
            "text": f"{budget} Другой тариф. {paid}",
        },
        {"source": 2, "url": "https://example.org/other", "text": paid},
    ]
    raw = json.dumps(
        {
            "status": "answered",
            "answer": "Разница составляет 358 рублей.",
            "citations": [
                {"source": 1, "quote": combined},
                {"source": 1, "quote": combined.replace("1517", "1518")},
                {"source": 1, "quote": f"{paid} ... {budget}"},
                {"source": 2, "quote": combined},
            ],
        },
        ensure_ascii=False,
    )
    parsed = parse_answer(raw, sources)["citations"]

    assert [(row["quote"], row["url"]) for row in parsed if row["verified"]] == [
        (budget, sources[0]["url"]),
        (paid, sources[0]["url"]),
    ]
    assert len([row for row in parsed if not row["verified"]]) == 3


def test_numeric_citation_does_not_start_or_end_inside_a_number():
    source = {
        "source": 1,
        "url": "https://example.org/fees",
        "text": "Бюджет — 1159 рублей. Платно — **1517** рублей. Отдельный тариф 159 рублей.",
    }
    quotations = [
        "Бюджет — 115",
        "517 рублей",
        "Бюджет — 1159 ... 517 рублей",
        "Платно — 1517",
        "159 рублей",
    ]
    raw = json.dumps(
        {
            "status": "answered",
            "answer": "В источнике есть разные тарифы.",
            "citations": [{"source": 1, "quote": quote} for quote in quotations],
        },
        ensure_ascii=False,
    )

    assert [row["verified"] for row in parse_answer(raw, [source])["citations"]] == [
        False,
        False,
        False,
        True,
        True,
    ]


def test_quote_cannot_drop_a_digit_group_from_a_ruble_amount():
    source = {
        "source": 1,
        "url": "https://example.org/fees",
        "text": "Плата — 4 500 рублей. Адрес: дом 15 214, вход со двора.",
    }
    raw = json.dumps(
        {
            "status": "answered",
            "answer": "Плата составляет 4 500 рублей.",
            "citations": [
                {"source": 1, "quote": quote}
                for quote in (
                    "500 рублей",
                    "Плата — 4",
                    "4 500 рублей",
                    "214, вход со двора",
                )
            ],
        },
        ensure_ascii=False,
    )

    assert [item["verified"] for item in parse_answer(raw, [source])["citations"]] == [
        False,
        False,
        True,
        True,
    ]
