"""Проверка стиля Python-кода проекта."""

import ast
import io
import itertools
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = [ROOT / "agent" / "src", ROOT / "agent" / "tests", ROOT / "scripts"]
SKIPPED_PARTS = {"__pycache__", ".venv", "mutants"}
TOOL_MARKERS = (
    "noqa",
    "type:",
    "pyright:",
    "fmt:",
    "ruff:",
    "pragma:",
    "isort:",
    "!",
    "-*-",
)
MIN_BLOCK_LINES = 3
MAX_DOCSTRING_LINES = 2
COMPOUND_STATEMENTS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)
ASSIGNMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign)
FLOW_STATEMENTS = (ast.Return, ast.Raise, ast.Break, ast.Continue, ast.Pass)


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def statement_kind(statement: ast.stmt) -> str:
    if isinstance(statement, COMPOUND_STATEMENTS):
        return "compound"

    if isinstance(statement, ASSIGNMENTS):
        return "assignment"

    if isinstance(statement, FLOW_STATEMENTS):
        return "flow"

    return type(statement).__name__


def first_line(statement: ast.stmt) -> int:
    decorators = getattr(statement, "decorator_list", [])

    return min([statement.lineno, *(decorator.lineno for decorator in decorators)])


def last_line(statement: ast.stmt) -> int:
    return statement.end_lineno or statement.lineno


def child_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []

    blocks = [
        getattr(statement, name)
        for name in ("body", "orelse", "finalbody")
        if getattr(statement, name, None)
    ]
    blocks += [handler.body for handler in getattr(statement, "handlers", [])]
    blocks += [case.body for case in getattr(statement, "cases", [])]

    return blocks


def needs_blank_line(previous: ast.stmt, current: ast.stmt) -> bool:
    kinds_differ = statement_kind(previous) != statement_kind(current)
    both_compound = statement_kind(previous) == "compound"

    return kinds_differ or both_compound


def blank_line_positions(statements: list[ast.stmt], lines: list[str]) -> list[int]:
    positions = []
    block_length = last_line(statements[-1]) - first_line(statements[0]) + 1

    if block_length >= MIN_BLOCK_LINES:
        for previous, current in itertools.pairwise(statements):
            start = first_line(current)
            already_separated = start - last_line(previous) > 1 and not lines[start - 2].strip()

            if needs_blank_line(previous, current) and not already_separated:
                positions.append(start)

    for statement in statements:
        for block in child_blocks(statement):
            positions += blank_line_positions(block, lines)

    return positions


def missing_blank_lines(tree: ast.Module, lines: list[str]) -> list[int]:
    positions = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positions += blank_line_positions(node.body, lines)

    return sorted(set(positions))


def module_docstring_problems(tree: ast.Module, path: str) -> list[Problem]:
    docstring = ast.get_docstring(tree, clean=False)

    if docstring is None:
        return [
            Problem(
                path,
                1,
                "нужен модульный докстринг: одна-две строки о том, для чего файл",
            )
        ]

    if not any("а" <= letter.lower() <= "я" or letter in "ёЁ" for letter in docstring):
        return [Problem(path, 1, "докстринг пишется по-русски")]

    lines = [line for line in docstring.split("\n") if line.strip()]

    if any(line.startswith("Содержит") for line in lines):
        return [Problem(path, 1, "в докстринге нет перечня содержимого: только о чём файл")]

    if len(lines) > MAX_DOCSTRING_LINES:
        return [Problem(path, 1, "модульный докстринг не длиннее двух строк")]

    return []


def nested_docstring_problems(tree: ast.Module, path: str) -> list[Problem]:
    problems = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node):
            problems.append(Problem(path, node.lineno, f"докстринг у функции {node.name}"))

        if isinstance(node, ast.ClassDef) and ast.get_docstring(node):
            problems.append(Problem(path, node.lineno, f"докстринг у класса {node.name}"))

    return problems


def comment_problems(source: str, path: str) -> list[Problem]:
    problems = []

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue

        text = token.string.lstrip("#").strip()

        if not text.startswith(TOOL_MARKERS):
            problems.append(
                Problem(
                    path,
                    token.start[0],
                    f"комментарий «{text}», выражай смысл названиями",
                )
            )

    return problems


def blank_line_problems(tree: ast.Module, lines: list[str], path: str) -> list[Problem]:
    return [
        Problem(path, line, "нужна пустая строка перед логическим блоком")
        for line in missing_blank_lines(tree, lines)
    ]


def check_source(source: str, path: str = "<source>") -> list[Problem]:
    if not source.strip():
        return []

    tree = ast.parse(source)
    lines = source.splitlines()

    return [
        *module_docstring_problems(tree, path),
        *nested_docstring_problems(tree, path),
        *comment_problems(source, path),
        *blank_line_problems(tree, lines, path),
    ]


def fix_source(source: str) -> str:
    if not source.strip():
        return source

    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    for position in reversed(missing_blank_lines(tree, [line.rstrip("\n") for line in lines])):
        lines.insert(position - 1, "\n")

    return "".join(lines)


def python_files(paths: list[Path]) -> list[Path]:
    found = []

    for path in paths:
        candidates = [path] if path.is_file() else sorted(path.rglob("*.py"))
        found += [file for file in candidates if not SKIPPED_PARTS & set(file.parts)]

    return found


def check_paths(paths: list[Path]) -> list[Problem]:
    problems = []

    for file in python_files(paths):
        problems += check_source(file.read_text(), str(file.relative_to(ROOT)))

    return problems


def fix_paths(paths: list[Path]) -> int:
    changed = 0

    for file in python_files(paths):
        source = file.read_text()
        fixed = fix_source(source)

        if fixed != source:
            file.write_text(fixed)
            changed += 1

    return changed


def main(arguments: list[str]) -> int:
    should_fix = "--fix" in arguments
    paths = [Path(argument).resolve() for argument in arguments if argument != "--fix"]
    targets = paths or DEFAULT_PATHS
    changed = fix_paths(targets) if should_fix else 0
    problems = check_paths(targets)

    for problem in problems:
        sys.stdout.write(f"{problem}\n")

    return 1 if problems or changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
