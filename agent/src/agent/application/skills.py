"""Выбирает прикладные инструкции для отдельного хода диалога."""

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent.domain.errors import AgentError

_SKILL_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")


class SkillRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = ""
    when: Literal["question", "attachment", "table", "web"] = "question"
    blocks: tuple[Literal["table", "office", "edition", "calculation"], ...] = ()
    events: bool = False
    legacy_prompt: Literal["", "WEB_ATTACHMENT_PROMPT", "WEB_TABLE_PROMPT"] = ""


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: dict[str, SkillRule]


@dataclass(frozen=True)
class ActiveSkills:
    ids: tuple[str, ...]
    instructions: tuple[str, ...]
    blocks: frozenset[str]
    events: bool


def select_skills(
    question: str,
    *,
    root: Path,
    overrides: Path,
    legacy: Callable[[str], str | None],
    attachment: bool,
    photo_only: bool,
    table: bool,
) -> ActiveSkills:
    ids = []
    instructions = []
    blocks = set()
    events = False
    available = {"question": True, "attachment": attachment, "table": table, "web": not photo_only}

    try:
        catalog = Catalog.model_validate(tomllib.loads((root / "catalog.toml").read_text("utf-8")))

        for name, rule in catalog.skills.items():
            if not _SKILL_ID.fullmatch(name) or not (rule.pattern or rule.when == "attachment"):
                raise ValueError("Invalid skill rule")

            if not available[rule.when] or (
                rule.pattern and not re.search(rule.pattern, question, re.I)
            ):
                continue

            path = overrides / name / "SKILL.md"
            legacy_text = legacy(rule.legacy_prompt) if rule.legacy_prompt else None
            text = (
                path.read_text("utf-8-sig")
                if path.is_file()
                else legacy_text or (root / name / "SKILL.md").read_text("utf-8-sig")
            ).strip()

            if not text:
                raise ValueError("Empty skill instructions")

            ids.append(name)
            instructions.append(text)
            blocks.update(rule.blocks)

            events = events or rule.events
    except (OSError, ValueError, re.error):
        raise AgentError("Не удалось загрузить навыки агента.", status=503) from None

    return ActiveSkills(tuple(ids), tuple(instructions), frozenset(blocks), events)
