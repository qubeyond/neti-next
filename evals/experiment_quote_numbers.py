"""Сверяет числовые цитаты и архивные совпадения без вызовов API."""

import argparse
import json
import re
from pathlib import Path

from agent.modules.answers.domain.evidence import parse_answer

NUMBER = re.compile(r"(?<!\d)\d{3,6}(?!\d)")
AMOUNT = re.compile(
    r"(?<!\d)\d{1,3}(?: \d{3})+(?= *(?:₽|руб(?:лей|ля|ль|\.)?(?!\w)))", re.IGNORECASE
)


def quotes_for(source: dict, *, currency: bool = False) -> tuple[set[str], set[str]]:
    if currency:
        text = source["text"]
        groups = list(AMOUNT.finditer(text))
        values = {match[0] for match in groups}
        cropped = {
            part
            for value in values
            for part in (value.split(" ", 1)[0], value.rsplit(" ", 1)[-1])
            if not any(
                not any(
                    amount.start() <= single.start() and single.end() <= amount.end()
                    for amount in groups
                )
                for single in re.finditer(rf"(?<!\d){part}(?!\d)", text)
            )
        }
        return values, cropped
    values = {match[0] for match in NUMBER.finditer(source["text"])}
    cropped = {
        part
        for value in values
        for part in (value[:-1], value[1:])
        if not re.search(rf"(?<!\d){part}(?!\d)", source["text"])
    }
    return values, cropped


def run(rows: list[dict], *, test_numbers: bool, currency: bool = False) -> dict:
    archived = []
    totals = {
        "numbers_accepted": 0,
        "numbers_total": 0,
        "cropped_accepted": 0,
        "cropped_total": 0,
    }
    for row in rows:
        generation = row.get("generation")
        if generation and row.get("sources"):
            checked = parse_answer(generation["answer"], row["sources"])
            archived.extend(
                (row["id"], citation["source"], citation["quote"])
                for citation in checked["citations"]
                if citation["verified"]
            )
        if not test_numbers:
            continue
        for source in row.get("sources", []):
            numbers, cropped = quotes_for(source, currency=currency)
            for category, examples in (("numbers", numbers), ("cropped", cropped)):
                if not examples:
                    continue
                raw = json.dumps(
                    {
                        "status": "answered",
                        "answer": "Контроль числовых цитат.",
                        "citations": [
                            {"source": source["source"], "quote": value}
                            for value in sorted(examples)
                        ],
                    },
                    ensure_ascii=False,
                )
                totals[f"{category}_total"] += len(examples)
                totals[f"{category}_accepted"] += sum(
                    item["verified"] for item in parse_answer(raw, [source])["citations"]
                )
    return {"totals": totals, "archived_verified": sorted(archived)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("e2e", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument("reading", type=Path)
    parser.add_argument("--currency", action="store_true")
    args = parser.parse_args()
    rows = {
        mode: json.loads(getattr(args, mode).read_text(encoding="utf-8"))["runs"]
        for mode in ("e2e", "oracle")
    }
    rows.update(json.loads(args.reading.read_text(encoding="utf-8"))["runs"])
    report = {
        mode: run(group, test_numbers=mode != "oracle", currency=args.currency)
        for mode, group in rows.items()
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
