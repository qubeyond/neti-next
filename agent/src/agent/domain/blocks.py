"""Проверяет ссылки ячеек и выполняет расчёт на подтверждённых числах."""

import re
from decimal import Decimal, DecimalException, localcontext
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, ValidationError

_NUMBER = re.compile(r"(?<![\d.,])[-+]?\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?(?!\d)")
_VALUE = r"^-?\d{1,12}(?:\.\d{1,6})?$"


class Cell(BaseModel):
    value: str = Field(min_length=1, max_length=270)
    ref: StrictInt = Field(ge=1)


class GridRow(BaseModel):
    cells: list[Cell | None]


class Grid(BaseModel):
    kind: Literal["table", "office", "edition"]
    title: str = Field(min_length=1, max_length=100)
    columns: list[str] = Field(min_length=1, max_length=6)
    rows: list[list[Cell | None] | GridRow] = Field(min_length=1, max_length=12)


class Operand(BaseModel):
    value: str = Field(pattern=_VALUE)
    ref: StrictInt = Field(ge=1)


class Formula(BaseModel):
    kind: Literal["calculation"]
    title: str = Field(min_length=1, max_length=100)
    operator: Literal["+", "-", "*", "/"]
    operands: tuple[Operand, Operand]


def _numbers(text: str) -> set[Decimal]:
    return {
        Decimal("".join(number.split()).replace(",", "."))
        for number in _NUMBER.findall(text)
        if len(number) <= 40
    }


def _grid(raw: dict, quotes: dict[int, list[str]]) -> dict | None:
    grid = Grid.model_validate(raw)
    rows = []

    for item in grid.rows:
        row = item.cells if isinstance(item, GridRow) else item

        if len(row) != len(grid.columns):
            continue

        checked = [
            cell
            if cell
            and cell.ref in quotes
            and _numbers(cell.value) <= _numbers(" ".join(quotes[cell.ref]))
            else None
            for cell in row
        ]

        if any(checked):
            rows.append(checked)

    return grid.model_copy(update={"rows": rows}).model_dump() if rows else None


def _calculation(raw: dict, quotes: dict[int, list[str]]) -> dict | None:
    formula = Formula.model_validate(raw)
    left, right = (Decimal(item.value) for item in formula.operands)

    if any(
        Decimal(item.value) not in _numbers(" ".join(quotes.get(item.ref, [])))
        for item in formula.operands
    ):
        return None

    with localcontext() as context:
        context.prec = 40

        match formula.operator:
            case "+":
                result = left + right
            case "-":
                result = left - right
            case "*":
                result = left * right
            case "/":
                result = (left / right).quantize(Decimal("0.000001"))

        return {**formula.model_dump(), "result": format(result.normalize(), "f")}


def verified_blocks(raw: object, citations: list[dict]) -> list[dict]:
    if not isinstance(raw, list):
        return []

    quotes: dict[int, list[str]] = {}

    for citation in citations:
        if citation["verified"]:
            quotes.setdefault(citation["ref"], []).append(citation["quote"])

    checked = []

    for item in raw[:4]:
        if not isinstance(item, dict):
            continue

        try:
            block = (
                _calculation(item, quotes)
                if item.get("kind") == "calculation"
                else _grid(item, quotes)
            )
        except (ValidationError, DecimalException):
            continue

        if block:
            checked.append(block)

    return checked
