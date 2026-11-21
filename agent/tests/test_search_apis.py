"""Проверяет запросы и нормализацию выдачи Exa."""

import json

from agent.config import DEFAULT_ENV_FILE, load_settings
from agent.providers.search_apis import _request, _results


def test_exa_requests_highlights_and_keeps_excerpts_distinct():
    settings = {**load_settings(DEFAULT_ENV_FILE), "EXA_SEARCH_OBJECTIVE": "Цель поиска для теста."}
    request = _request("exa", "Сроки поступления в НГУ", 5, "fake-key", settings)

    assert isinstance(request.content, bytes)
    assert json.loads(request.content) == {
        "query": "Сроки поступления в НГУ",
        "type": "auto",
        "numResults": 5,
        "contents": {"highlights": True},
        "objective": "Цель поиска для теста.",
    }
    assert request.headers["x-api-key"] == "fake-key"
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


def test_exa_uses_endpoint_and_objective_from_config(monkeypatch, tmp_path):
    from agent import config

    local = tmp_path / ".env"
    prompts = tmp_path / "prompts"

    local.write_text(
        'EXA_SEARCH_URL=https://search.example.test/exa\nEXA_SEARCH_OBJECTIVE="Из файла"\n',
        encoding="utf-8",
    )
    prompts.mkdir()
    (prompts / "exa-search-objective.txt").write_text(
        "Из файла промптов.\nСохрани {'query': 'пример'}.\n", encoding="utf-8"
    )

    monkeypatch.setattr(config, "ENV_FILE", local)
    monkeypatch.setattr(config, "PROMPTS_DIRECTORY", prompts)
    monkeypatch.delenv("EXA_SEARCH_URL", raising=False)
    monkeypatch.delenv("EXA_SEARCH_OBJECTIVE", raising=False)

    settings = load_settings(local)
    versioned = load_settings(DEFAULT_ENV_FILE)["EXA_SEARCH_OBJECTIVE"]

    assert settings["EXA_SEARCH_OBJECTIVE"] == "Из файла промптов.\nСохрани {'query': 'пример'}."
    assert settings["EXA_SEARCH_OBJECTIVE"] != versioned

    monkeypatch.setenv("EXA_SEARCH_OBJECTIVE", "Из окружения")

    request = _request("exa", "Вопрос", 5, "fake-key", settings)

    assert str(request.url) == "https://search.example.test/exa"
    assert json.loads(request.content)["objective"] == "Из окружения"
    assert json.loads(request.content)["contents"] == {"highlights": True}
