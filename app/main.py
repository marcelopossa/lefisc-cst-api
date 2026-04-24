"""
API FastAPI: consulta CST PIS/COFINS por NCM no Lefisc.

Endpoints:
- GET  /health                → status
- GET  /cst/{ncm}             → consulta individual: CST (1 ou 4) + dados auxiliares
- POST /cst/batch             → consulta em lote (até 20 NCMs por request)
- POST /cache/clear           → esvazia o cache SQLite
- POST /cache/purge-expired   → remove apenas entradas expiradas do cache
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
        "API que consulta o CST de PIS/COFINS de um NCM no site "
        "[Lefisc](https://www.lefisc.com.br) via scraping autenticado (Playwright) "
        "e devolve:\n\n"
        "- **CST = 1** quando o NCM possui alíquota de PIS/COFINS > 0 (tributado);\n"
        "- **CST = 4** quando é alíquota zero, monofásico ou isento.\n\n"
        "A decisão usa o trecho **'Não Contribuinte → Comerciante atacadista ou varejista'** "
        "da coluna PIS/COFINS do Lefisc.\n\n"
        "Além do CST, cada resposta inclui:\n"
        "- `confianca` (`alta` / `baixa`) + `revisao_necessaria` + `motivo_revisao` "
        "quando o parser detecta ambiguidade;\n"
        "- alíquotas cumulativo / não cumulativo de PIS e COFINS;\n"
        "- `raw_text` com **todas** as linhas da tabela do Lefisc (linha principal + "
        "linhas Ex 01, Ex 02…), contendo NCM, DESCRIÇÃO, IPI e PIS/COFINS — a coluna "
        "'DEMAIS INFORMAÇÕES' é omitida.\n\n"
        "**Cache**: respostas ficam em SQLite (sobrevive a restarts) com TTL configurável "
        "e purge periódico. Use `POST /cache/clear` para forçar re-consulta."
    ),
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health", summary="Health check")
async def health() -> dict:
    """Retorna `{\"status\": \"ok\"}` quando a API está de pé."""
    return {"status": "ok"}


@app.get(
    "/cst/{ncm}",
    response_model=CSTResponse,
    summary="Consulta CST PIS/COFINS de um NCM",
    description=(
        "Consulta um único NCM no Lefisc e retorna o CST (1 ou 4), alíquotas "
        "detectadas, flag de confiança, motivo de revisão (quando aplicável) e "
        "o `raw_text` com todas as linhas da tabela do Lefisc. "
        "Aceita NCM com ou sem formatação (ex: `48219000` ou `4821.90.00`). "
        "Resultado fica em cache pelo TTL configurado."
    ),
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
    summary="Consulta CST de vários NCMs em lote",
    description=(
        "Recebe uma lista de **1 a 20 NCMs** e devolve um item por NCM na mesma ordem "
        "do request. Falhas individuais (NCM inválido, timeout, sessão expirada) **não** "
        "abortam o batch — o item correspondente vem com `sucesso=false` e a mensagem "
        "de erro em `erro`. "
        "As consultas são serializadas internamente pelo scraper (o Lefisc usa sessão "
        "única), então o tempo total ≈ N × ~3s em cache miss, ou instantâneo quando "
        "todos os NCMs já estão em cache. "
        "A resposta agregada inclui contadores de sucesso/falha e de "
        "`acertos_alta_confianca` vs `casos_para_revisao`, facilitando triagem."
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


@app.post(
    "/cache/clear",
    summary="Esvazia o cache (todas as entradas)",
    description=(
        "Remove **todas** as entradas do cache SQLite, forçando re-consulta no "
        "Lefisc nas próximas chamadas. Útil após mudanças nos dados do Lefisc "
        "ou para debug. Retorna `{\"removidos\": N}`."
    ),
)
async def cache_clear() -> dict:
    removidos = limpar_cache()
    return {"removidos": removidos}


@app.post(
    "/cache/purge-expired",
    summary="Remove apenas entradas expiradas do cache",
    description=(
        "Varre o cache SQLite e remove só as entradas com TTL vencido. "
        "Roda automaticamente em background a cada `CACHE_PURGE_INTERVAL_HORAS` "
        "— este endpoint serve para disparar sob demanda. "
        "Retorna `{\"removidos\": N}`."
    ),
)
async def cache_purge_expired() -> dict:
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
