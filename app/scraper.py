"""
Scraper do Lefisc usando Playwright.

Fluxo (mapeado pelo vídeo do usuário):
1. Abre https://www.lefisc.com.br/
2. Faz login com usuário/senha
3. Navega até https://www.lefisc.com.br/ncm/conteudo.aspx
4. Preenche o campo de busca com o NCM
5. Clica em "Buscar"
6. Lê a coluna "PIS/COFINS" da linha mais específica (sub-NCM de 8 dígitos)
7. Retorna dados estruturados — a lógica de extração do trecho relevante
   e decisão CST fica em `app.parser`.

IMPORTANTE: Os seletores exatos (IDs/classes do DOM) ainda precisam ser
confirmados ao rodar a primeira vez com credenciais reais. Os seletores
abaixo foram inferidos do layout visto no vídeo e usam fallbacks por texto.
Ajustar após primeira execução se necessário.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import settings
from app.parser import (
    extrair_aliquotas,
    extrair_trecho_relevante,
    tem_pis_cofins,
)

logger = logging.getLogger(__name__)


class SessaoExpiradaError(RuntimeError):
    """Lefisc redirecionou para o modal de login no meio da consulta."""


@dataclass
class NCMResult:
    """Resultado extraído da página do Lefisc."""

    ncm: str
    descricao: Optional[str]
    pis_cofins_texto: str  # Texto bruto da célula PIS/COFINS inteira
    trecho_relevante: str  # "Não Contribuinte > Comerciante atac./varej."
    possui_pis_cofins: bool
    ipi_texto: Optional[str] = None  # Conteúdo da célula IPI
    linha_completa_texto: str = ""  # NCM + DESCRIÇÃO + IPI + PIS/COFINS (sem DEMAIS INFORMAÇÕES)
    aliquota_pis_cumulativo: Optional[str] = None
    aliquota_cofins_cumulativo: Optional[str] = None
    aliquota_pis_nao_cumulativo: Optional[str] = None
    aliquota_cofins_nao_cumulativo: Optional[str] = None


class LefiscScraper:
    """
    Scraper com sessão persistente — loga uma vez e reutiliza o contexto
    entre consultas pra ganhar performance.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._logged_in = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._playwright is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=settings.headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.browser_timeout_ms)

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._logged_in = False

    async def _ensure_login(self) -> None:
        """Faz login se ainda não estiver logado."""
        if self._logged_in:
            return

        assert self._page is not None
        page = self._page
        logger.info("Abrindo página de login do Lefisc")
        await page.goto(settings.lefisc_login_url, wait_until="domcontentloaded")

        try:
            # Aguarda Vue renderizar o botão (SPA — domcontentloaded não é suficiente)
            await page.wait_for_selector("button:has-text('Fazer Login')", state="visible", timeout=15000)
            await page.click("button:has-text('Fazer Login')")
            await page.wait_for_selector("#username", state="visible", timeout=10000)

            await page.fill("#username", settings.lefisc_username)
            await page.fill("#password", settings.lefisc_password)
            await page.locator("button.r").click()
            await page.wait_for_selector("#username", state="hidden", timeout=30000)
        except Exception as e:
            logger.warning("Seletores de login falharam — capturando screenshot: %s", e)
            await page.screenshot(path="login_debug.png")
            raise

        self._logged_in = True
        logger.info("Login realizado com sucesso")

    async def consultar_ncm(self, ncm: str) -> NCMResult:
        """
        Consulta um NCM no Lefisc. Serializa chamadas concorrentes pra usar
        uma única aba (o site é stateful).

        Se o Lefisc redirecionar para o modal de login no meio da consulta
        (sessão expirada), força re-login e tenta uma segunda vez.
        """
        async with self._lock:
            await self.start()
            for tentativa in range(2):
                try:
                    await self._ensure_login()
                    return await self._consultar_ncm_uma_vez(ncm)
                except SessaoExpiradaError:
                    logger.warning(
                        "Sessão do Lefisc expirada durante NCM %s — forçando re-login (tentativa %d)",
                        ncm, tentativa + 2,
                    )
                    self._logged_in = False
            # Se chegou aqui, os 2 tentativas viram sessão expirada — desiste
            raise SessaoExpiradaError(
                f"Sessão do Lefisc continua expirada após retry (NCM {ncm})"
            )

    async def _consultar_ncm_uma_vez(self, ncm: str) -> NCMResult:
        """Executa o fluxo de consulta sem retry. Lança SessaoExpiradaError
        se detectar redirecionamento para o modal de login."""
        assert self._page is not None
        page = self._page

        logger.info("Consultando NCM %s", ncm)
        await page.goto(settings.lefisc_ncm_url, wait_until="commit")
        await page.wait_for_timeout(1500)  # garante cookies após login SPA
        logger.info("URL após goto NCM: %s", page.url)
        await page.screenshot(path="ncm_debug.png")

        # Detecta sessão expirada: modal de login reabriu
        if await page.locator("#username").is_visible():
            raise SessaoExpiradaError("Modal de login visível após goto NCM")

        # Aguarda input (Vue pode demorar a renderizar)
        search_input = page.locator("input[placeholder*='NCM' i]").first
        try:
            await search_input.wait_for(state="visible", timeout=15000)
        except Exception:
            # Timeout no input é sintoma comum de sessão expirada
            if await page.locator("#username").is_visible():
                raise SessaoExpiradaError("Modal de login apareceu durante wait do input")
            raise

        # Botão "Buscar": get_by_role cobre <button>, <input type=submit>, role="button"
        # (has-text('Buscar') não encontrava por não ser <button> semântico)
        buscar_btn = page.get_by_role("button", name="Buscar", exact=True)
        await buscar_btn.wait_for(state="visible", timeout=15000)

        await search_input.fill(ncm)
        await buscar_btn.click()

        # Aguarda a tabela ter pelo menos uma linha de dados
        await page.wait_for_selector("table tr td", state="visible", timeout=30000)

        return await self._extrair_resultado(page, ncm)

    async def _extrair_resultado(self, page: Page, ncm_consultado: str) -> NCMResult:
        """Extrai dados da linha mais específica da tabela de resultado.

        Além da linha escolhida (usada na decisão CST), coleta *todas* as
        linhas válidas da tabela (inclui 'Ex 01', 'Ex 02' etc) para compor
        o `linha_completa_texto` — útil para auditoria manual.
        """
        rows = page.locator("table tr")
        total = await rows.count()
        if total == 0:
            raise ValueError(f"Nenhum resultado encontrado para NCM {ncm_consultado}")

        linhas_coletadas: list[dict] = []
        melhor_idx = -1
        melhor_score = -1

        for i in range(total):
            row = rows.nth(i)
            cells = row.locator("td")
            n_cells = await cells.count()
            if n_cells < 4:
                continue

            ncm_cell = (await cells.nth(0).inner_text()).strip()
            desc_cell = (await cells.nth(1).inner_text()).strip()
            ipi_cell = (await cells.nth(2).inner_text()).strip()
            pis_cell = (await cells.nth(3).inner_text()).strip()

            # HTML bruto de cada célula, para preservar <b>, <br>, <i>, <a> etc.
            ncm_html = (await cells.nth(0).inner_html()).strip()
            desc_html = (await cells.nth(1).inner_html()).strip()
            ipi_html = (await cells.nth(2).inner_html()).strip()
            pis_html = (await cells.nth(3).inner_html()).strip()

            linhas_coletadas.append({
                "ncm": ncm_cell,
                "descricao": desc_cell,
                "ipi_texto": ipi_cell,
                "pis_cofins_texto": pis_cell,
                "ncm_html": ncm_html,
                "descricao_html": desc_html,
                "ipi_html": ipi_html,
                "pis_html": pis_html,
            })

            # Prioriza linha com NCM formatado xxxx.xx.xx (8 dígitos)
            score = 0
            if re.match(r"^\d{4}\.\d{2}\.\d{2}$", ncm_cell):
                score += 10
            if pis_cell:
                score += 5
            if "regime" in pis_cell.lower():
                score += 3

            if score > melhor_score:
                melhor_score = score
                melhor_idx = len(linhas_coletadas) - 1

        if melhor_idx < 0:
            raise ValueError(f"Não foi possível extrair dados para NCM {ncm_consultado}")

        melhor_linha = linhas_coletadas[melhor_idx]
        texto_pc = melhor_linha["pis_cofins_texto"]
        ipi_cell = melhor_linha["ipi_texto"]
        trecho_relevante = extrair_trecho_relevante(texto_pc) or texto_pc
        aliquotas = extrair_aliquotas(trecho_relevante)

        # Monta tabela HTML com todas as linhas (sem "DEMAIS INFORMAÇÕES"),
        # reaproveitando o inner_html de cada célula para preservar a
        # formatação original do site (negrito, <br>, itálico, links, listas).
        linhas_html = [
            "<tr>"
            f"<td>{l['ncm_html']}</td>"
            f"<td>{l['descricao_html']}</td>"
            f"<td>{l['ipi_html']}</td>"
            f"<td>{l['pis_html']}</td>"
            "</tr>"
            for l in linhas_coletadas
        ]
        linha_completa = (
            "<table>"
            "<thead><tr>"
            "<th>NCM</th><th>DESCRIÇÃO</th><th>IPI</th><th>PIS/COFINS</th>"
            "</tr></thead>"
            "<tbody>" + "".join(linhas_html) + "</tbody>"
            "</table>"
        )

        return NCMResult(
            ncm=melhor_linha["ncm"],
            descricao=melhor_linha["descricao"],
            pis_cofins_texto=texto_pc,
            trecho_relevante=trecho_relevante,
            possui_pis_cofins=tem_pis_cofins(trecho_relevante),
            ipi_texto=ipi_cell,
            linha_completa_texto=linha_completa,
            **aliquotas,
        )


# Instância singleton usada pelo FastAPI via lifespan
scraper = LefiscScraper()
