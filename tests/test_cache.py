"""
Testes unitários do SQLiteCache (sem rede, sem Playwright).

Usa tmp_path do pytest pra cada teste ter seu próprio db isolado.
"""
import time

import pytest

from app.cache import SQLiteCache
from app.models import CSTResponse


def _fake_response(ncm: str = "4821.90.00", cst: int = 1) -> CSTResponse:
    return CSTResponse(
        ncm=ncm,
        cst=cst,
        possui_pis_cofins=(cst == 1),
        confianca="alta",
        revisao_necessaria=False,
        motivo_revisao=None,
        descricao="Test",
        raw_text="raw",
        trecho_relevante="trecho",
    )


def test_set_get_roundtrip(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=60)
    resp = _fake_response()
    cache["48219000"] = resp
    assert "48219000" in cache
    recuperado = cache["48219000"]
    assert recuperado.ncm == resp.ncm
    assert recuperado.cst == resp.cst
    assert recuperado.confianca == resp.confianca


def test_miss_raises_keyerror(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=60)
    with pytest.raises(KeyError):
        _ = cache["naoexiste"]


def test_ttl_expiry(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=0)
    cache["48219000"] = _fake_response()
    time.sleep(1.1)  # ttl=0 significa expira imediatamente
    assert "48219000" not in cache
    with pytest.raises(KeyError):
        _ = cache["48219000"]


def test_len_counts_only_valid(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=60)
    cache["a"] = _fake_response(ncm="a")
    cache["b"] = _fake_response(ncm="b")
    assert len(cache) == 2


def test_clear_returns_count(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=60)
    cache["a"] = _fake_response(ncm="a")
    cache["b"] = _fake_response(ncm="b")
    assert cache.clear() == 2
    assert len(cache) == 0


def test_overwrite_key(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=60)
    cache["48219000"] = _fake_response(cst=1)
    cache["48219000"] = _fake_response(cst=4)
    assert cache["48219000"].cst == 4
    assert len(cache) == 1


def test_persiste_entre_instancias(tmp_path):
    """A instância nova aberta no mesmo path deve ver os dados da anterior."""
    db_path = str(tmp_path / "cache.db")
    c1 = SQLiteCache(db_path, ttl_seconds=60)
    c1["48219000"] = _fake_response()
    del c1

    c2 = SQLiteCache(db_path, ttl_seconds=60)
    assert "48219000" in c2
    assert c2["48219000"].cst == 1


def test_purge_expired(tmp_path):
    cache = SQLiteCache(str(tmp_path / "cache.db"), ttl_seconds=0)
    cache["a"] = _fake_response(ncm="a")
    time.sleep(1.1)
    # novo put com TTL mais longo
    cache._ttl = 60
    cache["b"] = _fake_response(ncm="b")
    removidos = cache.purge_expired()
    assert removidos == 1
    assert "a" not in cache
    assert "b" in cache
