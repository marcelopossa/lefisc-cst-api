"""
API FastAPI: consulta CST por NCM no Lefisc.

Endpoints:
- GET  /health          → status
- GET  /cst/{ncm}       → retorna CST (1 ou 4) + dados auxiliares
- POST /cache/clear     → esvazia o cache (útil pra debug)
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse

from app.config import settings
from app.models import (
    BatchItem,
    BatchRequest,
    BatchResponse,
    CSTResponse,
    ErrorResponse,
)
from app.scraper import scraper
from app.service import consultar_cst, limpar_cache, purgar_expirados

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("lefisc-cst-api")


async def _purge_cache_loop(interval_seconds: int) -> None:
    """Loop periódico que remove entradas expiradas do cache SQLite."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            removidos = purgar_expirados()
            if removidos:
                logger.info("Purge periódico do cache: %d entrada(s) expirada(s) removida(s)", removidos)
            else:
                logger.debug("Purge periódico do cache: nada a remover")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falha no purge periódico do cache — segue executando")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia/encerra o browser e a task de purge junto com a API."""
    logger.info("Iniciando scraper (Playwright)...")
    await scraper.start()
    purge_interval = settings.cache_purge_interval_hours * 3600
    purge_task = asyncio.create_task(_purge_cache_loop(purge_interval))
    logger.info(
        "Purge periódico do cache ativo: a cada %dh", settings.cache_purge_interval_hours
    )
    try:
        yield
    finally:
        purge_task.cancel()
        try:
            await purge_task
        except asyncio.CancelledError:
            pass
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


@app.post(
    "/cst/batch",
    response_model=BatchResponse,
    summary="Consulta CST de vários NCMs em uma só chamada",
    description=(
        "Recebe uma lista de NCMs e retorna um item por NCM. "
        "Falhas individuais (NCM inválido, timeout) não abortam o batch — "
        "o item correspondente vem com sucesso=false e a mensagem de erro. "
        "As consultas são serializadas internamente pelo scraper (Lefisc "
        "usa sessão única), então tempo total ≈ N × ~3s em cache miss."
    ),
)
async def post_cst_batch(req: BatchRequest) -> BatchResponse:
    tarefas = [consultar_cst(ncm) for ncm in req.ncms]
    respostas = await asyncio.gather(*tarefas, return_exceptions=True)

    itens: list[BatchItem] = []
    sucessos = falhas = alta = review = 0
    for ncm, res in zip(req.ncms, respostas):
        if isinstance(res, BaseException):
            falhas += 1
            itens.append(
                BatchItem(ncm_consultado=ncm, sucesso=False, resultado=None, erro=str(res))
            )
            continue
        sucessos += 1
        if res.revisao_necessaria:
            review += 1
        else:
            alta += 1
        itens.append(
            BatchItem(ncm_consultado=ncm, sucesso=True, resultado=res, erro=None)
        )

    return BatchResponse(
        total=len(req.ncms),
        sucessos=sucessos,
        falhas=falhas,
        acertos_alta_confianca=alta,
        casos_para_revisao=review,
        resultados=itens,
    )


@app.post("/cache/clear")
async def cache_clear() -> dict:
    removidos = limpar_cache()
    return {"removidos": removidos}


@app.post("/cache/purge-expired")
async def cache_purge_expired() -> dict:
    """Remove só as entradas expiradas. Roda automaticamente a cada
    CACHE_PURGE_INTERVAL_HORAS — este endpoint é útil pra disparar sob demanda."""
    removidos = purgar_expirados()
    return {"removidos": removidos}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
