import pytest

from evals.research_notes import (
    build_entry,
    experiment_entry,
    updated_journal,
    write_record,
)

JOURNAL = (
    "# Эксперименты\n\n## Сводка\n\n| ID | Название |\n| --- | --- |\n"
    "| E01 | Старое |\n\n## E01 · Старое\n\nСтарый результат.\n"
    "\n## Обзор\n\nЭтот раздел сохраняется.\n"
)


def test_record_updates_summary_and_detail_without_touching_other_sections(tmp_path):
    path = tmp_path / "experiments.md"
    path.write_text(JOURNAL, encoding="utf-8")
    row, body = build_entry(
        "E02",
        {
            "title": "Пилот",
            "result": "3/6",
            "cost": "0,20 ₽",
            "decision": "Повторить",
            "details": "Шесть пар.",
        },
    )
    write_record(path, "E02", row, body)
    created = path.read_text(encoding="utf-8")
    write_record(path, "E02", row, body)
    assert path.read_text(encoding="utf-8") == created
    assert "3/6" in experiment_entry(created, "E02")
    changed = updated_journal(created, "E02", row.replace("3/6", "4/6"), body)
    assert changed.count("| E02 |") == changed.count("## E02 ·") == 1
    assert "4/6" in experiment_entry(changed, "E02")
    assert "Этот раздел сохраняется." in changed


def test_record_refuses_inconsistent_existing_entry():
    with pytest.raises(ValueError, match="Неполная"):
        updated_journal(JOURNAL.replace("| E01 | Старое |\n", ""), "E01", "row", "body")
