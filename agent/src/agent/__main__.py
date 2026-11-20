import argparse
import json
from urllib.parse import quote

from . import AgentError, ask
from .service import answer_question


def main() -> None:
    parser = argparse.ArgumentParser(description="Текстовый агент RouterAI")
    parser.add_argument("question", help="Вопрос агенту")
    parser.add_argument("--model", help="Идентификатор модели RouterAI")
    parser.add_argument(
        "--web", action="store_true", help="Один поиск перед генерацией ответа"
    )
    parser.add_argument("--json", action="store_true", help="Ответ и метрики в JSON")
    options = parser.parse_args()

    try:
        if options.web:
            result = answer_question(options.question, model=options.model)
            if result["status"] == "invalid":
                raise AgentError("Не удалось разобрать ответ веб-агента.")
        else:
            result = ask(options.question, model=options.model)
    except AgentError as error:
        parser.exit(1, f"Ошибка: {error}\n")

    if options.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["answer"])
        for citation in result.get("citations", []):
            if citation["verified"]:
                label = citation["quote"].replace("[", "\\[").replace("]", "\\]")
                url = quote(citation["url"], safe=":/?&=#%+;,@!$'*-._~")
                print(f"\n[«{label}»]({url})")


if __name__ == "__main__":
    main()
