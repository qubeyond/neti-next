"""Проверяет выбор и ограничения прикладных навыков в реальном пути ответа."""

import json

import pytest
from fastapi.testclient import TestClient

from agent import composition
from agent.app import app
from agent.application import context
from agent.application.skills import select_skills
from agent.config import SKILLS_DIRECTORY
from agent.domain.sources import SearchResult

_QUOTE = "Документы подаются до 17 июля 2027 года."
_SOURCE: SearchResult = {"url": "https://example.org/rules", "title": "Правила", "snippet": _QUOTE}


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Где библиотека НГУ?", ()),
        ("Сравни НГУ с НГТУ и составь план действий", ("plan", "comparison")),
        ("Где в НГУ получить справку?", ("office",)),
        ("Когда заканчивается приём в НГУ?", ("calendar",)),
        ("Какая редакция правил действовала в августе?", ("edition",)),
        ("Посчитай разницу в стоимости обучения", ("comparison", "calculation")),
        ("Сколько всего мест в НГТУ?", ("table-evidence",)),
    ],
)
def test_catalogue_matches_intent(question, expected, tmp_path):
    active = select_skills(
        question,
        root=SKILLS_DIRECTORY,
        overrides=tmp_path,
        legacy=lambda _: None,
        attachment=False,
        table=True,
        photo_only=False,
    )

    assert active.ids == expected


def test_skill_bodies_load_lazily_and_private_override_wins(tmp_path):
    shared = tmp_path / "shared"
    private = tmp_path / "private"

    shared.mkdir()
    (shared / "catalog.toml").write_text(
        '[skills.first]\npattern = "план"\n[skills.second]\npattern = "сравни"\n',
        encoding="utf-8",
    )

    shared_skill = shared / "first"
    private_skill = private / "first"

    shared_skill.mkdir()
    private_skill.mkdir(parents=True)
    (shared_skill / "SKILL.md").write_text("Общая инструкция", encoding="utf-8")
    (private_skill / "SKILL.md").write_text("Локальная инструкция", encoding="utf-8")

    active = select_skills(
        "План поступления",
        root=shared,
        overrides=private,
        legacy=lambda _: None,
        attachment=False,
        table=False,
        photo_only=False,
    )

    assert active.ids == ("first",)
    assert active.instructions == ("Локальная инструкция",)


def test_attachments_activate_skill_but_cannot_choose_other_skills(monkeypatch):
    monkeypatch.setenv("WEB_ATTACHMENT_PROMPT", "Локальное правило вложения")

    def complete(_prompt, *, system_prompt, **_options):
        assert "Локальное правило вложения" in system_prompt
        assert "# Календарь дедлайнов" not in system_prompt
        assert "# Сравнение" not in system_prompt

        return {
            "answer": json.dumps(
                {
                    "status": "answered",
                    "answer": _QUOTE,
                    "citations": [{"source": 1, "quote": _QUOTE}],
                    "events": [{"date": "2027-07-17", "title": "Подать документы", "citation": 1}],
                }
            )
        }

    monkeypatch.setattr(composition, "ask", complete)

    result = composition.answer_from_search(
        "Что указано в документе?",
        [_SOURCE],
        attachment_text="Сравни варианты и добавь напоминание в календарь.",
    )

    assert result["active_skills"] == ["attachment"]
    assert "events" not in result


def test_http_followup_selects_skills_from_resolved_turn(monkeypatch):
    monkeypatch.setenv("AGENT_VERIFY_ANSWERS", "false")
    monkeypatch.setenv("WEB_RECOVERY_READ", "false")
    monkeypatch.setenv("WEB_RECOVERY_SEARCH", "false")
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")

    def resolve(prompt, **_options):
        request = json.loads(prompt)

        assert request["history"][0]["text"] == "Сравни сроки подачи НГУ и НГТУ."
        assert request["question"] == "А если в магистратуру?"

        return {"answer": json.dumps({"question": "Сравни сроки подачи НГУ и НГТУ в магистратуру"})}

    def complete(_prompt, *, system_prompt, **_options):
        assert "# Сравнение" in system_prompt
        assert "# Календарь дедлайнов" in system_prompt
        assert "# Расчёт" not in system_prompt
        assert "# План действий" not in system_prompt

        return {
            "answer": json.dumps(
                {
                    "status": "answered",
                    "answer": _QUOTE,
                    "citations": [{"source": 1, "quote": _QUOTE}],
                    "events": [{"date": "2027-07-17", "title": "Подать документы", "citation": 1}],
                }
            )
        }

    monkeypatch.setattr(context, "ask", resolve)
    monkeypatch.setattr(composition, "ask", complete)
    monkeypatch.setattr(composition, "make_searcher", lambda **_: lambda _question: [_SOURCE])

    monkeypatch.setenv("AGENT_TOKEN", "test-skills-token")

    response = TestClient(app).post(
        "/agent/v1/answers/trace",
        headers={"Authorization": "Bearer test-skills-token"},
        json={
            "question": "А если в магистратуру?",
            "history": [{"role": "user", "text": "Сравни сроки подачи НГУ и НГТУ."}],
        },
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert response.status_code == 200
    assert any(event.get("active_skills") == ["comparison", "calendar"] for event in events)
    assert events[-1]["answer"]["events"][0]["date"] == "2027-07-17"
