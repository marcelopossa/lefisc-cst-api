"""Modelos Pydantic para request/response."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class CSTResponse(BaseModel):
    """Resposta do endpoint de consulta CST."""

    ncm: str = Field(..., description="NCM consultado (pode estar normalizado)")
    cst: int = Field(..., description="CST resultante: 1 (tributado) ou 4 (alíquota zero/monofásico)")
    possui_pis_cofins: bool = Field(..., description="Se possui alíquota PIS/COFINS > 0")
    confianca: Literal["alta", "baixa"] = Field(
        ...,
        description=(
            "'alta' = regra aplicada com clareza; "
            "'baixa' = parsing ambíguo, revisar manualmente"
        ),
    )
    revisao_necessaria: bool = Field(
        ..., description="True quando confianca='baixa' — caso deve ser revisado"
    )
    motivo_revisao: Optional[str] = Field(
        None, description="Explica por que a confiança é baixa (ausente quando alta)"
    )
    descricao: Optional[str] = Field(None, description="Descrição do NCM")
    aliquota_pis_cumulativo: Optional[str] = Field(None, description="Ex: '0,65%'")
    aliquota_cofins_cumulativo: Optional[str] = Field(None, description="Ex: '3,00%'")
    aliquota_pis_nao_cumulativo: Optional[str] = Field(None, description="Ex: '1,65%'")
    aliquota_cofins_nao_cumulativo: Optional[str] = Field(None, description="Ex: '7,6%'")
    raw_text: Optional[str] = Field(
        None,
        description=(
            "Bloco completo da coluna PIS/COFINS do Lefisc — retornado em "
            "todas as consultas para permitir auditoria e revisão manual."
        ),
    )
    trecho_relevante: Optional[str] = Field(
        None,
        description=(
            "Subtrecho usado na decisão CST (ex: 'Não Contribuinte > "
            "Comerciante atacadista ou varejista' ou seção VAREJISTA em "
            "formato A/B/C/D)."
        ),
    )


class ErrorResponse(BaseModel):
    """Resposta de erro padronizada."""

    error: str
    detail: Optional[str] = None
