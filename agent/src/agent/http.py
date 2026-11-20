from fastapi import FastAPI, HTTPException

app = FastAPI(title="NETI Next Agent", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/agent/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/v1/answers")
def create_answer() -> None:
    raise HTTPException(503, "Агент ещё не подключён.")
