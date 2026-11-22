"""Читает и обновляет отдельные результаты циклов исследования."""

import argparse
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

REPO = Path(__file__).resolve().parents[1]
JOURNAL = REPO / "docs/experiments.md"
SUMMARY = "Сводка"
EXPERIMENT_ID = re.compile(r"E[0-9]{2,}")
SECTION = re.compile(r"(?m)^## ([^\n]+)\n")


def sections(document: str) -> list[tuple[str, int, int]]:
    headings = list(SECTION.finditer(document))
    return [
        (heading[1], heading.start(), following.start() if following else len(document))
        for heading, following in zip(headings, [*headings[1:], None], strict=True)
    ]


def bounds(document: str, title: str, *, prefix: bool = False) -> tuple[int, int]:
    matches = [
        (start, end)
        for name, start, end in sections(document)
        if (name.startswith(title) if prefix else name == title)
    ]
    if len(matches) != 1:
        raise ValueError(f"Раздел должен встретиться ровно один раз: {title}")
    return matches[0]


def summary_rows(document: str) -> list[str]:
    start, end = bounds(document, SUMMARY)
    return [line for line in document[start:end].splitlines() if re.match(r"^\| E\d+ \|", line)]


def experiment_entry(document: str, experiment: str) -> str:
    start, end = bounds(document, f"{experiment} · ", prefix=True)
    row = next(
        (line for line in summary_rows(document) if line.startswith(f"| {experiment} |")),
        "",
    )
    return f"{row}\n\n{document[start:end].strip()}".strip()


def build_entry(experiment: str, record: dict) -> tuple[str, str]:
    if not EXPERIMENT_ID.fullmatch(experiment):
        raise ValueError("Идентификатор должен иметь вид E112.")
    fields = ("title", "result", "cost", "decision", "details")
    if not isinstance(record, dict) or any(
        not isinstance(record.get(key), str) or not record[key].strip() for key in fields
    ):
        raise ValueError(f"Нужны непустые строки: {', '.join(fields)}.")
    cells = [record[key].strip() for key in fields[:-1]]
    if any("\n" in cell or "\r" in cell for cell in cells):
        raise ValueError("Заголовок и ячейки сводки должны занимать одну строку.")
    if SECTION.search(record["details"]):
        raise ValueError("В деталях используй заголовки третьего уровня или глубже.")
    row = "| " + " | ".join([experiment, *(cell.replace("|", "\\|") for cell in cells)]) + " |"
    body = f"## {experiment} · {record['title'].strip()}\n\n{record['details'].strip()}"
    return row, body


def updated_journal(document: str, experiment: str, row: str, body: str) -> str:
    start, end = bounds(document, SUMMARY)
    summary = document[start:end].rstrip()
    matching = [other for other in summary_rows(document) if other.startswith(f"| {experiment} |")]
    heading = f"{experiment} · "
    detail_count = sum(name.startswith(heading) for name, _, _ in sections(document))
    if len(matching) != detail_count or detail_count > 1:
        raise ValueError(f"Неполная или дублирующаяся запись {experiment}.")
    summary = summary.replace(matching[0], row, 1) if matching else f"{summary}\n{row}"
    document = document[:start] + summary + "\n\n" + document[end:]
    if matching:
        start, end = bounds(document, heading, prefix=True)
        return document[:start] + body + ("\n\n" + document[end:] if end < len(document) else "\n")
    return document.rstrip() + "\n\n" + body + "\n"


@contextmanager
def journal_lock(path: Path):
    import fcntl

    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def write_record(path: Path, experiment: str, row: str, body: str) -> None:
    with journal_lock(path):
        text = path.read_text(encoding="utf-8")
        changed = updated_journal(text, experiment, row, body)
        if changed == text:
            return
        swap = None
        try:
            with NamedTemporaryFile(
                dir=path.parent,
                prefix=".research-notes-",
                encoding="utf-8",
                mode="w",
                delete=False,
            ) as target:
                swap = Path(target.name)
                target.write(changed)
            swap.chmod(path.stat().st_mode & 0o777)
            swap.replace(path)
        finally:
            if swap is not None:
                swap.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    commands = parser.add_subparsers(dest="command", required=True)
    recent = commands.add_parser("recent", help="Показать последние строки сводки.")
    recent.add_argument("--limit", type=int, default=5)
    show = commands.add_parser("show", help="Показать одну запись с деталями.")
    show.add_argument("experiment")
    metrics = commands.add_parser("metrics", help="Показать раздел Markdown-отчёта.")
    metrics.add_argument("report", type=Path)
    metrics.add_argument("section")
    raw = commands.add_parser("json", help="Вывести вложенный ключ из JSON-отчёта.")
    raw.add_argument("report", type=Path)
    raw.add_argument("--key", default="summary", help="Путь через точку, например summary.cascade.")
    record = commands.add_parser("record", help="Вставить или обновить запись журнала по ID.")
    record.add_argument("experiment")
    record.add_argument("input", type=Path, help="JSON: title, result, cost, decision, details.")
    record.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "recent":
            if args.limit < 1:
                raise ValueError("Лимит должен быть положительным.")
            print("\n".join(summary_rows(args.journal.read_text(encoding="utf-8"))[-args.limit :]))
        elif args.command == "show":
            print(experiment_entry(args.journal.read_text(encoding="utf-8"), args.experiment))
        elif args.command == "metrics":
            report = args.report.read_text(encoding="utf-8")
            start, end = bounds(report, args.section)
            print(report[start:end].rstrip())
        elif args.command == "json":
            value = json.loads(args.report.read_text(encoding="utf-8"))
            for key in args.key.split("."):
                value = value[key]
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            row, body = build_entry(args.experiment, payload)
            if args.dry_run:
                updated_journal(
                    args.journal.read_text(encoding="utf-8"), args.experiment, row, body
                )
                print(f"{row}\n\n{body}")
            else:
                write_record(args.journal, args.experiment, row, body)
                print(f"Запись {args.experiment} обновлена.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
