"""Modelos Pydantic para request/response."""
from typing import Optional
from pydantic import BaseModel, Field


class CSTResponse(BaseModel):
    """Resposta do endpoint de consulta CST."""

    ncm: str = Field(..., description="NCM consultado (pode estar normalizado)")
    cst: int = Field(..., description="CST resultante: 1 (tributado) ou 4 (alíquota zero/monofásico)")
    possui_pis_cofins: bool = Field(..., description="Se possui alíquota PIS/COFINS > 0")
    descricao: Optional[str] = Field(None, description="Descrição do NCM")
    aliquota_pis_cumulativo: Optional[str] = Field(None, description="Ex: '0,65%'")
    aliquota_cofins_cumulativo: Optional[str] = Field(None, description="Ex: '3,00%'")
    aliquota_pis_nao_cumulativo: Optional[str] = Field(None, description="Ex: '1,65%'")
    aliquota_cofins_nao_cumulativo: Optional[str] = Field(None, description="Ex: '7,6%'")
    raw_text: Optional[str] = Field(None, description="Texto bruto da coluna PIS/COFINS (debug)")
    trecho_relevante: Optional[str] = Field(
        None,
        description=(
            "Trecho usado na decisão: 'Não Contribuinte > Comerciante "
            "atacadista ou varejista' (debug)"
        ),
    )


class ErrorResponse(BaseModel):
    """Resposta de erro padronizada."""

    error: str
    detail: Optional[str] = None
