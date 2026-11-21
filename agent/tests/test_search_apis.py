"""Проверяет запросы и нормализацию выдачи Exa."""

import json

from agent.modules.answers.infrastructure.search_apis import _request, _results


def test_exa_requests_highlights_and_keeps_excerpts_distinct():
    request = _request("exa", "Сроки поступления в НГУ", 5, "fake-key", "")

    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {
        "query": "Сроки поступления в НГУ",
        "type": "auto",
        "numResults": 5,
        "contents": {"highlights": True},
        "objective": (
            "Найди прямые официальные страницы и документы нужного вуза, "
            "которые подтверждают ответ на вопрос. Подбери выдержки с нужными датами, "
            "фактами и условиями. Учитывай филиал, программу и год, если они указаны."
        ),
    }
    assert request.get_header("X-api-key") == "fake-key"
    assert _results(
        "exa",
        {
            "results": [
                {
                    "url": "https://example.edu/terms",
                    "title": "Сроки",
                    "highlights": [
                        "Приём начинается 20 июня.",
                        "Документы принимают онлайн.",
                    ],
                },
                {"url": "https://example.edu/contacts", "title": "Контакты"},
            ]
        },
        5,
    ) == [
        {
            "url": "https://example.edu/terms",
            "title": "Сроки",
            "snippet": "Приём начинается 20 июня. … Документы принимают онлайн.",
        },
        {"url": "https://example.edu/contacts", "title": "Контакты", "snippet": ""},
    ]
