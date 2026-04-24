# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos comuns

```bash
# Rodar a API em modo dev (hot reload)
uvicorn app.main:app --reload
# ou
python -m app.main

# Instalar deps (inclui download do Chromium para Playwright)
pip install -r requirements.txt
python -m playwright install chromium

# Testes unitários do parser (não dependem de rede/Playwright)
pytest -q

# Rodar um único teste
pytest tests/test_parser.py::nome_do_teste -q

# Forçar re-consulta após mudar dados no Lefisc
curl -X POST http://127.0.0.1:8000/cache/clear

# Debug visual do scraper (ver o browser)
HEADLESS=false uvicorn app.main:app --reload
```

Ambiente WSL: o Python do sistema é PEP 668 externally-managed — sempre use a venv em `.venv/` antes de instalar dependências.

## Arquitetura (big picture)

API FastAPI que consulta o **CST PIS/COFINS** de um NCM no site autenticado [lefisc.com.br](https://www.lefisc.com.br/) via scraping com Playwright e retorna **1** (tributado) ou **4** (alíquota zero/monofásico).

### Camadas e fluxo de uma requisição

```
HTTP → app/main.py → app/service.py → app/scraper.py → app/parser.py
         (FastAPI)    (cache TTL)      (Playwright)     (regras puras)
```

- **`app/main.py`** — endpoints FastAPI. Usa `lifespan` para iniciar/encerrar o singleton do scraper junto com a API (uma única sessão Playwright reaproveitada entre requisições).
- **`app/service.py`** — orquestração: cache TTL (cachetools), dedupe de chamadas concorrentes para o mesmo NCM, e transformação do `NCMResult` do scraper no `CSTResponse` final (inclui confidence scoring e flag `needs_review`).
- **`app/scraper.py`** — `LefiscScraper` é um singleton com `asyncio.Lock` que **serializa** chamadas (o Lefisc é stateful: uma única aba, sessão persistente). Fluxo: login SPA (Vue) → navega para página de NCM → preenche busca → escolhe a "linha mais específica" da tabela via score heurístico (prioriza NCM formatado `xxxx.xx.xx`).
- **`app/parser.py`** — funções **puras e testáveis** (sem Playwright/rede). Responsável por extrair o trecho relevante do texto bruto da célula PIS/COFINS, calcular alíquotas, decidir CST 1 vs 4, e computar confidence score + flag de review.
- **`app/models.py`** — modelos Pydantic de request/response (inclui `confidence` como `Literal` e `needs_review: bool`).
- **`app/config.py`** — `Settings` via `pydantic-settings` lendo `.env` (credenciais, URLs, timeouts, cache).

### Regra de negócio crítica

A coluna PIS/COFINS pode ter múltiplas seções (Contribuinte, Não Contribuinte, Importador, Industrial…). A API **sempre** usa:

```
Não Contribuinte → Comerciante atacadista ou varejista
```

Dentro desse trecho:
- Qualquer alíquota **> 0** → **CST 1** (`possui_pis_cofins=true`)
- "Alíquota Zero" / "Isento" / "Monofásico" / só `0,00%` → **CST 4** (`possui_pis_cofins=false`)

Quando o parser detecta múltiplas seções ou ambiguidade, o response inclui `confidence` baixo e `needs_review=true`.

### Singleton + lifespan

Existe **uma única instância** de `LefiscScraper` (exportada como `scraper` em `app/scraper.py`) gerenciada pelo `lifespan` do FastAPI. Não crie novas instâncias em handlers. O `_lock` interno garante que requisições concorrentes não briguem pela mesma aba.

## Particularidades do site Lefisc

O site é uma **SPA em Vue.js** — `domcontentloaded` **não é suficiente**, é preciso esperar explicitamente pelos elementos renderizados:
- Login: aguardar `button:has-text('Fazer Login')` → clicar → aguardar `#username` visível → preencher → `button.r` (classe única do submit, evita conflito com widget RD Station).
- Página NCM não-autenticada **redireciona para modal de login** — sempre checar login antes de navegar.
- Após login SPA há delay para cookies propagarem (3s) antes de ir para a URL de NCM.
- Timeouts generosos (60-90s) são intencionais: conexões via VPN são instáveis e o JS do Lefisc demora a renderizar.

Se seletores quebrarem, o scraper salva `login_debug.png` e `ncm_debug.png` no diretório raiz para inspeção.

## Coisas a fazer / evitar

- **Antes de mexer em `scraper.py`**: rode com `HEADLESS=false` pra ver o que está acontecendo. Screenshots em `login_debug.png` / `ncm_debug.png` são suas melhores amigas.
- **Não mockar o Playwright em testes** — toda lógica testável deve ficar em `app/parser.py` (funções puras). Para fixtures, capture o `raw_text` real de uma resposta (`GET /cst/{ncm}`) e adicione em `tests/fixtures.json`.
- **Não faça rate limit agressivo ao Lefisc** — o site pode banir IP. Use o cache TTL (padrão 24h) agressivamente.
- O `.env` contém credenciais reais — **nunca** commitar. Já está no `.gitignore`.

## TODOs conhecidos (ver README)

Seletores de login podem precisar ajuste se o Lefisc mudar o layout; falta retry com backoff em sessão expirada; falta rate limiting interno; cache ainda é em memória (perde entre restarts — considerar SQLite se isso virar um problema).
