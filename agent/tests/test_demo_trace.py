"""Проверяет привязку диагностических событий и защищённые блоки ответа."""

import json

from fastapi.testclient import TestClient

from agent.app import create_app
from agent.domain.blocks import verified_blocks
from agent.domain.grounding import check_answer
from agent.runtime import phase


def test_trace_is_authenticated_and_carries_parent_and_metrics(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "demo-key")
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")

    def answer(question):
        assert question == "Вопрос"

        with phase("generation"):
            with phase("generate", service="inference") as trace:
                trace["usage"] = {"total_tokens": 17}

        return {"status": "clarification", "answer": "Какой вуз?", "citations": []}

    api = TestClient(create_app(answer))
    path = "/agent/v1/answers/trace"
    request = {"question": "Вопрос"}

    assert api.post(path, json=request).status_code == 401

    response = api.post(path, json=request, headers={"Authorization": "Bearer demo-key"})
    events = [json.loads(line) for line in response.text.splitlines()]

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    assert [item["type"] for item in events] == [
        "span_start",
        "span_start",
        "span_end",
        "span_end",
        "answer",
    ]
    assert events[1]["parent"] == events[0]["id"]
    assert events[2]["usage"] == {"total_tokens": 17}
    assert events[-1]["answer"]["suggestions"] == ["НГУ", "НГТУ НЭТИ", "НГПУ"]


def test_blocks_ignore_invented_values_and_calculate_with_cited_operands():
    sources = [
        {"ref": 1, "verified": True, "quote": "Стоимость первого месяца — 1 200 рублей."},
        {"ref": 2, "verified": True, "quote": "Стоимость второго месяца — 800 рублей."},
    ]
    blocks = [
        {
            "kind": "table",
            "title": "Тарифы",
            "columns": ["Первый", "Второй"],
            "rows": [
                {
                    "cells": [
                        {"value": "1 200 рублей", "ref": 1},
                        {"value": "1 000 рублей", "ref": 2},
                    ]
                }
            ],
        },
        {
            "kind": "calculation",
            "title": "Сумма",
            "operator": "+",
            "operands": [{"value": "1200", "ref": 1}, {"value": "800", "ref": 2}],
            "result": "5000",
        },
    ]
    table, calculation = verified_blocks(blocks, sources)

    assert table["rows"] == [[{"value": "1 200 рублей", "ref": 1}, None]]
    assert calculation["result"] == "2000"


def test_checked_result_can_be_used_in_text_but_model_claim_cannot():
    source = {"ref": 1, "verified": True, "url": "https://example.org", "quote": "1200 и 800"}
    blocks = verified_blocks(
        [
            {
                "kind": "calculation",
                "title": "Два месяца",
                "operator": "+",
                "operands": [{"value": "1200", "ref": 1}, {"value": "800", "ref": 1}],
            },
        ],
        [source],
    )
    result = {
        "status": "answered",
        "citations": [source],
        "malformed_citations": 0,
        "blocks": blocks,
    }

    assert (
        check_answer("Какова сумма?", {**result, "answer": "Сумма — 2000 рублей."})["status"]
        == "answered"
    )
    assert (
        check_answer("Какова сумма?", {**result, "answer": "Сумма — 2500 рублей."})["status"]
        == "unverified"
    )
