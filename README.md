# Lefisc CST API

API em FastAPI que consulta o **CST PIS/COFINS** de um NCM no site
[lefisc.com.br](https://www.lefisc.com.br/) (Legislação Fiscal) e retorna:

- **1** quando o NCM possui alíquota de PIS/COFINS (tributado)
- **4** quando não possui (alíquota zero / monofásico)

## Regra de negócio

A coluna PIS/COFINS do Lefisc às vezes tem mais de uma seção
(Contribuinte, Não Contribuinte, Importador, Industrial...). A API sempre usa:

```
Não Contribuinte → Comerciante atacadista ou varejista
```

E dentro desse trecho:

- Se houver qualquer alíquota > 0 → **CST 1**
- Se for "Alíquota Zero", "Isento", "Monofásico" ou só 0,00% → **CST 4**

## Estrutura

```
lefisc-cst-api/
├── app/
│   ├── __init__.py
│   ├── main.py          ← FastAPI + endpoints
│   ├── config.py        ← Settings (.env)
│   ├── models.py        ← Pydantic request/response
│   ├── parser.py        ← Regras de parsing (sem Playwright) - testável
│   ├── scraper.py       ← Playwright: login + busca no Lefisc
│   └── service.py       ← Cache + orquestração
├── tests/
│   └── test_parser.py   ← Testes unitários do parser
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Setup local

```bash
# 1) Clonar e entrar
cd lefisc-cst-api

# 2) Criar venv
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 3) Instalar dependências
pip install -r requirements.txt
python -m playwright install chromium

# 4) Configurar credenciais
cp .env.example .env
# Edite .env com LEFISC_USERNAME e LEFISC_PASSWORD

# 5) Rodar
python -m app.main
# ou
uvicorn app.main:app --reload
```

API sobe em `http://127.0.0.1:8000` — docs Swagger em
`http://127.0.0.1:8000/docs`.

## Endpoints

### `GET /cst/{ncm}`

```bash
curl http://127.0.0.1:8000/cst/48219000
```

Resposta:

```json
{
  "ncm": "4821.90.00",
  "cst": 1,
  "possui_pis_cofins": true,
  "descricao": "-Outras",
  "aliquota_pis_cumulativo": "0,65%",
  "aliquota_cofins_cumulativo": "3,00%",
  "aliquota_pis_nao_cumulativo": "1,65%",
  "aliquota_cofins_nao_cumulativo": "7,6%",
  "raw_text": "<table><thead><tr><th>NCM</th><th>DESCRIÇÃO</th><th>IPI</th><th>PIS/COFINS</th></tr></thead><tbody><tr><td>4821.90.00</td><td>-Outras</td><td>...</td><td>...</td></tr></tbody></table>",
  "trecho_relevante": "..."
}
```

### `POST /cst/batch`

Consulta vários NCMs em uma chamada. Falhas individuais não abortam o
batch — o item vem com `sucesso=false` e a mensagem de erro.

```bash
curl -X POST http://127.0.0.1:8000/cst/batch \
  -H "Content-Type: application/json" \
  -d '{"ncms": ["48219000", "22030000", "84148021"]}'
```

Resposta:

```json
{
  "total": 3,
  "sucessos": 3,
  "falhas": 0,
  "acertos_alta_confianca": 1,
  "casos_para_revisao": 2,
  "resultados": [
    { "ncm_consultado": "48219000", "sucesso": true, "resultado": { ... } },
    { "ncm_consultado": "22030000", "sucesso": true, "resultado": { ... } },
    { "ncm_consultado": "84148021", "sucesso": true, "resultado": { ... } }
  ]
}
```

> **Formato do `raw_text`**: HTML (`<table>`) com as mesmas colunas e formatação
> (negrito, `<br>`, links) do site do Lefisc. Excluímos a coluna "DEMAIS
> INFORMAÇÕES" para reduzir tamanho. Útil para auditoria manual — qualquer
> viewer/webhook consumer que renderize HTML mostra a tabela igualzinha ao site.

As consultas são serializadas internamente (Lefisc usa sessão única), então
o tempo total ≈ `N × ~3s` em cache miss. Cache hit é instantâneo.

**Limite: 20 NCMs por request** (protege contra pressão excessiva no Lefisc
e risco de ban de IP). Se precisar consultar mais, divida em batches.

Caso o Lefisc redirecione para o modal de login durante a consulta (sessão
expirada), o scraper detecta, força re-login e repete a tentativa uma vez
antes de retornar erro.

### `GET /health`

```bash
curl http://127.0.0.1:8000/health
```

### `POST /cache/clear`

Limpa o cache em memória — útil se você atualizou alíquotas no Lefisc
e quer forçar re-consulta.

## Testes

```bash
pytest -q
```

Os testes do parser (`tests/test_parser.py`) não dependem de Playwright
nem de rede — validam a regra de negócio isoladamente.

## Migração para Claude Code

Este projeto foi prototipado no Cowork. Para continuar o desenvolvimento
sério no Claude Code:

1. **Mover a pasta para um local de trabalho**
   ```bash
   mv lefisc-cst-api ~/projetos/
   cd ~/projetos/lefisc-cst-api
   ```

2. **Inicializar Git**
   ```bash
   git init
   git add .
   git commit -m "chore: estrutura inicial da API"
   ```

3. **Abrir no Claude Code**
   ```bash
   claude
   ```

4. **Primeiros passos no Claude Code**
   - Ajustar seletores de login quando tiver as credenciais reais
     (rodar uma vez com `HEADLESS=false` pra ver o fluxo)
   - Capturar amostras reais de HTML/texto de NCMs variados (usar
     `raw_text` do response) e adicionar como fixtures em `tests/`
   - Adicionar tratamento de expiração de sessão (re-login automático)
   - Opcional: persistir cache em SQLite pra não perder entre restarts

## TODOs conhecidos

- [ ] Confirmar seletores exatos do formulário de login (primeira execução)
- [ ] Confirmar estrutura da tabela de resultado — a lógica atual assume
      colunas na ordem `NCM | DESCRIÇÃO | IPI | PIS/COFINS | DEMAIS INFO`
- [ ] Adicionar retry com backoff em caso de sessão expirada
- [ ] Capturar screenshots em erros (já existe pra login, falta pra busca)
- [ ] Rate limiting interno (Lefisc pode banir IP se consultar demais)
