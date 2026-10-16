"""
degradacao.py
=============

Cálculo da **degradação acumulada** de cada escada rolante ao longo das leituras.

Modelo
------
Cada nota de manutenção datada gera um *dano* (ver :func:`dano_evento`). Os danos
são somados por mês para cada escada, formando a sequência de leituras
``d[0], d[1], …, d[n-1]``. A degradação acumulada segue a recorrência::

    D(i) = d[i] + a · D(i-1) + b · D(i-2)        D(-1) = D(-2) = 0

* ``d[i]`` é o dano novo do mês ``i``;
* ``a`` e ``b`` (padrão 0,6 e 0,2) são a fração do desgaste dos meses anteriores
  que permanece – a manutenção recupera parte dele. Como ``a + b < 1``, o
  desgaste antigo vai sendo "esquecido" (meia-vida ≈ 4 meses).

A recorrência tem **dois** termos recursivos, então a versão recursiva pura
recalcula os mesmos subproblemas um número exponencial de vezes – é o caso
clássico em que memoização e tabulação fazem diferença.

Três implementações, com o mesmo resultado numérico:

* :func:`degradacao_recursiva`  – recursão pura, O(φⁿ) chamadas
* :func:`degradacao_memoizada`  – recursão + dicionário de memo, O(n)
* :func:`degradacao_tabulada`   – iterativa com tabela, O(n), sem recursão
"""
from __future__ import annotations

import math
import sys
from typing import Sequence

import numpy as np
import pandas as pd

A_PADRAO = 0.6
B_PADRAO = 0.2

# --------------------------------------------------------------------------- #
# Dano de cada evento
# --------------------------------------------------------------------------- #
PESO_TIPO = {
    "Z1": 2.0,   # falha corretiva (85 % deixam a escada indisponível)
    "Z7": 1.5,   # reparo de componente estrutural (tramo, degrau)
    "Z2": 1.0,   # anomalia detectada sem parada (ruído, óleo baixo…)
}
DEFEITOS_GRAVES = ("QUEBRAD", "DANIFICAD", "DESGASTAD", "TRAVAMENTO", "ROMPID",
                   "TRINCAD", "SUPERAQUEC", "QUEIMAD")


def dano_evento(tipo: str, indisponivel: int, falha: int,
                duracao_min: float | None, defeito: str | None) -> float:
    """Dano de uma única nota de manutenção.

    ``dano = peso_tipo + 2·indisponível + 1·falha + min(ln(1 + horas_paradas), 3)
    + ajuste_defeito``

    * ``peso_tipo``: Z1 = 2, Z7 = 1,5, Z2 = 1; outros tipos (preventiva Z3,
      melhorias Z8/Z9) não são desgaste e valem 0.
    * ``horas_paradas`` entra em log para que uma parada de 3 dias não pese 70×
      uma de 1 hora; o teto 3 equivale a ~19 h.
    * ``ajuste_defeito``: +1 para defeitos graves (quebrado, danificado,
      desgastado…), −0,5 para "NADA CONSTATADO". O dano mínimo de um evento
      válido é 0,5.

    Returns:
        float: dano do evento (0 para tipos que não representam desgaste).
    """
    base = PESO_TIPO.get(tipo)
    if base is None:
        return 0.0
    d = base + 2.0 * indisponivel + 1.0 * falha
    if duracao_min is not None and not (isinstance(duracao_min, float) and math.isnan(duracao_min)):
        d += min(math.log1p(duracao_min / 60.0), 3.0)
    if isinstance(defeito, str):
        if any(g in defeito for g in DEFEITOS_GRAVES):
            d += 1.0
        elif "NADA CONSTATADO" in defeito:
            d -= 0.5
    return max(d, 0.5)


def adicionar_dano(eventos: pd.DataFrame) -> pd.DataFrame:
    """Devolve uma cópia de ``eventos`` com a coluna ``dano`` calculada."""
    ev = eventos.copy()
    ev["dano"] = [
        dano_evento(t, i, f, dmin, dfe)
        for t, i, f, dmin, dfe in zip(ev["tipo"], ev["indisponivel"], ev["falha"],
                                      ev["duracao_min"], ev["defeito"])
    ]
    return ev


def leituras_mensais(eventos: pd.DataFrame, inicio: str = "2010-01",
                     fim: str | None = None) -> pd.DataFrame:
    """Soma o dano por escada e por mês.

    Returns:
        DataFrame com uma linha por escada e uma coluna por mês (``Period``),
        meses sem evento preenchidos com 0. Cada linha é a sequência de leituras
        ``d[0..n-1]`` daquela escada.
    """
    ev = eventos[eventos["dano"] > 0].copy()
    ev["mes"] = pd.to_datetime(ev["inicio"]).dt.to_period("M")
    fim = fim or str(ev["mes"].max())
    meses = pd.period_range(inicio, fim, freq="M")
    tab = ev.pivot_table(index="escada", columns="mes", values="dano",
                         aggfunc="sum", fill_value=0.0)
    return tab.reindex(columns=meses, fill_value=0.0)


# --------------------------------------------------------------------------- #
# 1) Recursiva pura
# --------------------------------------------------------------------------- #
def degradacao_recursiva(danos: Sequence[float], i: int | None = None,
                         a: float = A_PADRAO, b: float = B_PADRAO,
                         contador: dict | None = None) -> float:
    """Degradação acumulada ``D(i)`` por **recursão pura**.

    Cada chamada abre duas outras (``i-1`` e ``i-2``), então o número de
    chamadas cresce como Fibonacci: ~1,6ⁿ. Use só para ``n`` pequeno (≤ 30).

    Args:
        danos: sequência de danos mensais ``d[0..n-1]``.
        i: índice da leitura (padrão: a última, ``n-1``).
        a, b: coeficientes de permanência do desgaste.
        contador: dicionário opcional; ``contador["chamadas"]`` é incrementado
            a cada chamada, para medir o custo.

    Returns:
        float: ``D(i)``.
    """
    if i is None:
        i = len(danos) - 1
    if contador is not None:
        contador["chamadas"] = contador.get("chamadas", 0) + 1
    if i < 0:
        return 0.0
    return (danos[i]
            + a * degradacao_recursiva(danos, i - 1, a, b, contador)
            + b * degradacao_recursiva(danos, i - 2, a, b, contador))


# --------------------------------------------------------------------------- #
# 2) Recursiva memoizada (dicionário)
# --------------------------------------------------------------------------- #
def degradacao_memoizada(danos: Sequence[float], i: int | None = None,
                         a: float = A_PADRAO, b: float = B_PADRAO,
                         memo: dict | None = None,
                         contador: dict | None = None) -> float:
    """Degradação acumulada ``D(i)`` por **recursão com memoização**.

    O dicionário ``memo`` guarda ``D(k)`` já calculado; cada índice é resolvido
    uma única vez, então o custo cai para O(n) chamadas. A profundidade da
    recursão é ``n``, por isso o limite de recursão do Python é elevado quando
    necessário.

    Args:
        danos, i, a, b, contador: como em :func:`degradacao_recursiva`.
        memo: dicionário ``{índice: D(índice)}``; pode ser reaproveitado entre
            chamadas para obter a série inteira sem recálculo.

    Returns:
        float: ``D(i)``.
    """
    if i is None:
        i = len(danos) - 1
        if i + 100 > sys.getrecursionlimit():
            sys.setrecursionlimit(i + 1000)
    if memo is None:
        memo = {}
    if contador is not None:
        contador["chamadas"] = contador.get("chamadas", 0) + 1
    if i < 0:
        return 0.0
    if i in memo:
        return memo[i]
    memo[i] = (danos[i]
               + a * degradacao_memoizada(danos, i - 1, a, b, memo, contador)
               + b * degradacao_memoizada(danos, i - 2, a, b, memo, contador))
    return memo[i]


# --------------------------------------------------------------------------- #
# 3) Iterativa com tabulação
# --------------------------------------------------------------------------- #
def degradacao_tabulada(danos: Sequence[float], a: float = A_PADRAO,
                        b: float = B_PADRAO, serie: bool = False,
                        contador: dict | None = None):
    """Degradação acumulada por **tabulação** (bottom-up, sem recursão).

    Preenche a tabela ``D[0..n-1]`` da esquerda para a direita; cada posição
    usa as duas anteriores, já prontas. O(n) tempo, sem risco de estouro de
    pilha.

    Args:
        danos, a, b: como em :func:`degradacao_recursiva`.
        serie: se ``True`` devolve a lista completa ``[D(0), …, D(n-1)]``.
        contador: se informado, ``contador["passos"]`` recebe o nº de iterações.

    Returns:
        float ``D(n-1)`` ou a lista completa, conforme ``serie``.
    """
    n = len(danos)
    tabela = [0.0] * n
    for i in range(n):
        anterior1 = tabela[i - 1] if i >= 1 else 0.0
        anterior2 = tabela[i - 2] if i >= 2 else 0.0
        tabela[i] = danos[i] + a * anterior1 + b * anterior2
    if contador is not None:
        contador["passos"] = n
    if serie:
        return tabela
    return tabela[-1] if n else 0.0


# --------------------------------------------------------------------------- #
# Ranking de criticidade
# --------------------------------------------------------------------------- #
def ranking_criticidade(leituras: pd.DataFrame, eventos: pd.DataFrame,
                        escadas: pd.DataFrame, a: float = A_PADRAO,
                        b: float = B_PADRAO, meses_recentes: int = 12) -> pd.DataFrame:
    """Ordena as escadas pela degradação acumulada na última leitura.

    A criticidade é ``D(n-1)`` calculado pela versão tabulada. Para apoiar a
    leitura do ranking, a tabela traz também eventos, falhas Z1 e horas de
    indisponibilidade nos últimos ``meses_recentes`` meses.

    Returns:
        DataFrame ordenado do mais crítico para o menos crítico, com a coluna
        ``posicao`` (1 = mais crítica).
    """
    crit = {esc: degradacao_tabulada(linha.to_numpy(), a, b)
            for esc, linha in leituras.iterrows()}
    rk = pd.DataFrame({"escada": list(crit), "criticidade": list(crit.values())})

    ev = eventos.copy()
    ev["inicio"] = pd.to_datetime(ev["inicio"])
    corte = ev["inicio"].max() - pd.DateOffset(months=meses_recentes)
    rec = ev[(ev["inicio"] > corte) & (ev["dano"] > 0)]
    resumo = rec.groupby("escada").agg(
        eventos_12m=("nota", "size"),
        falhas_z1_12m=("tipo", lambda s: int((s == "Z1").sum())),
        horas_paradas_12m=("duracao_min",
                           lambda s: float(s[rec.loc[s.index, "indisponivel"] == 1].sum() / 60)),
    )
    rk = (rk.merge(escadas[["escada", "estacao"]], on="escada", how="left")
            .merge(resumo, on="escada", how="left")
            .fillna({"eventos_12m": 0, "falhas_z1_12m": 0, "horas_paradas_12m": 0.0}))
    rk = rk.sort_values("criticidade", ascending=False).reset_index(drop=True)
    rk.insert(0, "posicao", np.arange(1, len(rk) + 1))
    rk["eventos_12m"] = rk["eventos_12m"].astype(int)
    rk["falhas_z1_12m"] = rk["falhas_z1_12m"].astype(int)
    rk["horas_paradas_12m"] = rk["horas_paradas_12m"].round(1)
    rk["criticidade"] = rk["criticidade"].round(2)
    return rk[["posicao", "escada", "estacao", "criticidade", "eventos_12m",
               "falhas_z1_12m", "horas_paradas_12m"]]
