"""Проверяет, что выводы и действия действительно поддержаны доказательствами."""

import json
from collections.abc import Callable

from agent.domain.errors import AgentError
from agent.domain.evidence import json_object
from agent.providers.inference import ask
from agent.runtime import phase

Verification = Callable[[str, dict], tuple[bool, dict | None]]


def audit(
    question: str, answer: dict, *, prompt: str, budget: int, settings: dict
) -> tuple[bool, dict | None]:
    citations = [
        {"ref": item["ref"], "source": item["source"], "quote": item["quote"], "url": item["url"]}
        for item in answer["citations"]
        if item.get("verified")
    ]

    with phase("evidence_audit"):
        generation = ask(
            json.dumps(
                {
                    "question": question,
                    "answer": answer["answer"],
                    "citations": citations,
                    "blocks": answer.get("blocks") or [],
                    "events": answer.get("events") or [],
                    "search_fragments": answer.get("sources") or [],
                },
                ensure_ascii=False,
            ),
            settings=settings,
            system_prompt=prompt,
            response_format={"type": "json_object"},
            reasoning_enabled=False,
            max_tokens=budget,
            allow_empty=True,
        )

    try:
        checked = json_object(generation["answer"]).get("supported") is True
    except (ValueError, TypeError, KeyError):
        checked = False

    return checked, generation


def verify_answer(verify: Verification | None, question: str, result: dict) -> dict:
    if verify is None or result["status"] not in {"answered", "partial"}:
        return result

    try:
        with phase("verify") as trace:
            verdict, operation = verify(question, result)
            trace["decision"] = "supported" if verdict else "unverified"
    except AgentError:
        verdict, operation = False, None

    record = {"audit": operation} if operation is not None else {}

    if verdict:
        return {**result, **record}

    return {
        **result,
        **record,
        "status": "unverified",
        "answer": "Найденные источники не позволяют уверенно подтвердить ответ на этот вопрос.",
        "citations": [],
        "blocks": [],
        "events": [],
        "grounding_failure": "audit",
    }
