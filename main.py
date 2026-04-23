"""
API FastAPI: consulta CST por NCM no Lefisc.

Endpoints:
- GET  /health          → status
- GET  /cst/{ncm}       → retorna CST (1 ou 4) + dados auxiliares
- POST /cache/clear     → esvazia o cache (útil pra debug)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse

from app.config import settings
from app.models import CSTResponse, ErrorResponse
from app.scraper import scraper
from app.service import consultar_cst, limpar_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("lefisc-cst-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia/encerra o browser junto com a API."""
    logger.info("Iniciando scraper (Playwright)...")
    await scraper.start()
    try:
        yield
    finally:
        logger.info("Encerrando scraper...")
        await scraper.stop()


app = FastAPI(
    title="Lefisc CST API",
    description=(
        "Consulta o CST PIS/COFINS de um NCM no Lefisc. "
        "Retorna 1 quando possui PIS/COFINS, 4 caso contrário."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get(
    "/cst/{ncm}",
    response_model=CSTResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def get_cst(
    ncm: str = Path(..., description="NCM (completo ou incompleto)", example="48219000"),
) -> CSTResponse:
    try:
        return await consultar_cst(ncm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Falha ao consultar NCM %s", ncm)
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")


@app.post("/cache/clear")
async def cache_clear() -> dict:
    removidos = limpar_cache()
    return {"removidos": removidos}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
