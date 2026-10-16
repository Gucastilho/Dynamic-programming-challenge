"""
planejamento.py
===============

Plano de manutenção do turno: :func:`planejar_turno` junta o **ranking de
criticidade** e as **rotas** calculadas no grafo para dizer quais escadas atender,
em que ordem e por qual caminho, sem estourar as horas do turno.

Como a seleção é feita
----------------------
1. **Candidatas**: as ``top_n`` escadas mais críticas do ranking.
2. **Tempo de reparo** de cada escada: mediana histórica das falhas que a
   deixaram indisponível (:func:`tempos_reparo`), mais 10 min de acesso interno.
3. **Mochila por grupos (programação dinâmica)**: cada estação é um grupo; a
   opção *k* do grupo é "atender as *k* escadas mais críticas daquela estação",
   com custo = ida e volta da base (Dijkstra) + soma dos reparos e valor = soma
   das criticidades. A DP escolhe a combinação de opções de maior valor que cabe
   no orçamento ``horas × 60``. O custo de ida e volta por estação é
   conservador: a rota real, que encadeia as estações, nunca é mais cara.
4. **Rota real**: as estações escolhidas são visitadas pela ordem do vizinho
   mais próximo (distâncias de Dijkstra) e a volta à base é incluída.
5. **Refinamento**: o tempo que sobrou (porque a rota real é mais curta que a
   estimativa) é usado para incluir mais escadas críticas, uma a uma, enquanto
   couber no turno.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd

from .grafo import Grafo, carregar_rede, dijkstra, reconstruir_rota
from .degradacao import adicionar_dano, leituras_mensais, ranking_criticidade

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACESSO_INTERNO_MIN = 10       # deslocamento dentro da estação até a escada
REPARO_MIN, REPARO_MAX = 20, 240


def tempos_reparo(eventos: pd.DataFrame, meses: int = 24) -> dict:
    """Tempo de reparo previsto (min) por escada.

    Mediana da duração das falhas que deixaram a escada indisponível nos
    últimos ``meses`` meses, limitada a [20, 240] min. Escada sem histórico usa
    a mediana da estação; estação sem histórico usa a mediana geral. Soma-se
    ``ACESSO_INTERNO_MIN``.
    """
    ev = eventos.copy()
    ev["inicio"] = pd.to_datetime(ev["inicio"])
    corte = ev["inicio"].max() - pd.DateOffset(months=meses)
    base = ev[(ev["inicio"] > corte) & (ev["indisponivel"] == 1) & ev["duracao_min"].notna()]
    geral = base["duracao_min"].median()
    por_est = base.groupby("estacao")["duracao_min"].median()
    por_esc = base.groupby("escada")["duracao_min"].median()
    saida = {}
    for esc, est in ev.drop_duplicates("escada")[["escada", "estacao"]].itertuples(index=False):
        t = por_esc.get(esc, por_est.get(est, geral))
        saida[esc] = int(round(min(max(t, REPARO_MIN), REPARO_MAX))) + ACESSO_INTERNO_MIN
    return saida


@lru_cache(maxsize=1)
def carregar_contexto(pasta_data: str = os.path.join(RAIZ, "data")) -> dict:
    """Monta grafo, ranking e tempos de reparo a partir dos arquivos de ``data/``.

    É chamado automaticamente por :func:`planejar_turno` quando nenhum
    contexto é passado, para que a função funcione só com ``(base, horas)``.
    """
    eventos = adicionar_dano(pd.read_csv(os.path.join(pasta_data, "eventos_escadas.csv")))
    escadas = pd.read_csv(os.path.join(pasta_data, "escadas.csv"))
    grafo = carregar_rede(os.path.join(pasta_data, "rede.json"), escadas)
    ranking = ranking_criticidade(leituras_mensais(eventos), eventos, escadas)
    return {"grafo": grafo, "ranking": ranking, "reparo": tempos_reparo(eventos)}


def _distancias(g: Grafo, vertices) -> dict:
    """Menores tempos (Dijkstra) entre todos os pares de ``vertices``."""
    return {v: dijkstra(g, v) for v in vertices}


def _ordem_vizinho_mais_proximo(base: str, estacoes: list[str], dist: dict) -> list[str]:
    ordem, atual, restantes = [], base, set(estacoes)
    while restantes:
        prox = min(restantes, key=lambda s: (dist[atual][0][s], s))
        ordem.append(prox)
        restantes.remove(prox)
        atual = prox
    return ordem


def _custo_tour(base: str, ordem: list[str], dist: dict) -> float:
    pontos = [base] + ordem + [base]
    return sum(dist[u][0][v] for u, v in zip(pontos, pontos[1:]))


def _mochila_por_grupos(grupos: dict, orcamento: int) -> dict:
    """Mochila de múltipla escolha: no máximo uma opção por grupo.

    Args:
        grupos: ``{estação: [(custo_k, valor_k), …]}`` com k = 1, 2, …
        orcamento: minutos disponíveis.

    Returns:
        ``{estação: k}`` com o número de escadas escolhido por estação.
    """
    nomes = list(grupos)
    # tabela[i][t] = melhor valor usando os i primeiros grupos com t minutos
    tabela = [[0.0] * (orcamento + 1) for _ in range(len(nomes) + 1)]
    escolha = [[0] * (orcamento + 1) for _ in range(len(nomes) + 1)]
    for i, est in enumerate(nomes, start=1):
        for t in range(orcamento + 1):
            melhor, k_melhor = tabela[i - 1][t], 0
            for k, (custo, valor) in enumerate(grupos[est], start=1):
                if custo <= t and tabela[i - 1][t - custo] + valor > melhor:
                    melhor, k_melhor = tabela[i - 1][t - custo] + valor, k
            tabela[i][t], escolha[i][t] = melhor, k_melhor
    # backtracking
    resultado, t = {}, orcamento
    for i in range(len(nomes), 0, -1):
        k = escolha[i][t]
        if k:
            resultado[nomes[i - 1]] = k
            t -= grupos[nomes[i - 1]][k - 1][0]
    return resultado


def planejar_turno(base: str, horas: float, contexto: dict | None = None,
                   top_n: int = 40, hora_inicio: str = "07:00") -> dict:
    """Indica quais escadas atender no turno, em que ordem e por qual caminho.

    Args:
        base: vértice de partida e chegada da equipe (ex.: ``"BASE"``).
        horas: duração do turno.
        contexto: ``{"grafo", "ranking", "reparo"}``; se omitido, é montado a
            partir de ``data/`` por :func:`carregar_contexto`.
        top_n: quantas escadas do topo do ranking são candidatas.
        hora_inicio: horário de saída da base, para montar a agenda.

    Returns:
        dict com ``paradas`` (ordem, estação, rota, chegada, escadas atendidas),
        ``retorno``, ``tempo_total_min``, ``orcamento_min``,
        ``criticidade_atendida`` e ``escadas_atendidas``.
    """
    ctx = contexto or carregar_contexto()
    g, ranking, reparo = ctx["grafo"], ctx["ranking"], ctx["reparo"]
    orcamento = int(horas * 60)
    if base not in g.adj:
        raise ValueError(f"Base {base!r} não existe no grafo")

    cand = ranking.head(top_n)
    cand = cand[cand["criticidade"] > 0]
    estacoes = sorted(cand["estacao"].unique())
    dist = _distancias(g, [base] + estacoes)

    # 1) Mochila por grupos com custo conservador (ida e volta por estação)
    grupos, itens = {}, {}
    for est, grp in cand.groupby("estacao"):
        lista = [(r.escada, float(r.criticidade), reparo[r.escada])
                 for r in grp.sort_values("criticidade", ascending=False).itertuples()]
        itens[est] = lista
        ida_volta = 2 * dist[base][0][est]
        opcoes, custo, valor = [], ida_volta, 0.0
        for _, crit, rep in lista:
            custo += rep
            valor += crit
            opcoes.append((int(math.ceil(custo)), valor))
        grupos[est] = opcoes
    escolha = _mochila_por_grupos(grupos, orcamento)
    selecionadas = {est: itens[est][:k] for est, k in escolha.items()}

    def custo_total(sel: dict) -> float:
        ordem = _ordem_vizinho_mais_proximo(base, [s for s in sel if sel[s]], dist)
        return _custo_tour(base, ordem, dist) + sum(rep for l in sel.values() for *_, rep in l)

    # 2) Refinamento: usa a folga da rota real para incluir mais escadas
    for r in cand.itertuples():
        if any(r.escada == e for l in selecionadas.values() for e, *_ in l):
            continue
        teste = {k: list(v) for k, v in selecionadas.items()}
        teste.setdefault(r.estacao, []).append((r.escada, float(r.criticidade), reparo[r.escada]))
        if custo_total(teste) <= orcamento:
            selecionadas = teste

    # 3) Monta a agenda
    ordem = _ordem_vizinho_mais_proximo(base, [s for s in selecionadas if selecionadas[s]], dist)
    relogio = datetime.strptime(hora_inicio, "%H:%M")
    t, atual, paradas = 0.0, base, []
    for n, est in enumerate(ordem, start=1):
        rota = reconstruir_rota(dist[atual][1], atual, est)
        desloc = dist[atual][0][est]
        t += desloc
        chegada = relogio + timedelta(minutes=t)
        servicos = []
        for esc, crit, rep in sorted(selecionadas[est], key=lambda x: -x[1]):
            servicos.append({"escada": esc, "criticidade": round(crit, 2),
                             "inicio": (relogio + timedelta(minutes=t)).strftime("%H:%M"),
                             "reparo_min": rep})
            t += rep
        paradas.append({"ordem": n, "estacao": est, "nome": g.nomes[est], "rota": rota,
                        "deslocamento_min": desloc, "chegada": chegada.strftime("%H:%M"),
                        "escadas": servicos})
        atual = est
    rota_volta = reconstruir_rota(dist[atual][1], atual, base) if atual != base else [base]
    volta = dist[atual][0][base] if atual != base else 0.0
    t += volta
    atendidas = [s for p in paradas for s in p["escadas"]]
    plano = {
        "base": base, "horas": horas, "orcamento_min": orcamento,
        "paradas": paradas,
        "retorno": {"rota": rota_volta, "deslocamento_min": volta,
                    "chegada": (relogio + timedelta(minutes=t)).strftime("%H:%M")},
        "tempo_total_min": round(t, 1),
        "tempo_deslocamento_min": round(sum(p["deslocamento_min"] for p in paradas) + volta, 1),
        "tempo_reparo_min": sum(s["reparo_min"] for s in atendidas),
        "escadas_atendidas": len(atendidas),
        "criticidade_atendida": round(sum(s["criticidade"] for s in atendidas), 2),
    }
    assert plano["tempo_total_min"] <= orcamento + 1e-9, "plano estourou o turno"
    return plano


def exibir_plano(plano: dict) -> None:
    """Imprime o plano do turno em formato de agenda."""
    print(f"Turno de {plano['horas']} h saindo de {plano['base']} "
          f"(orçamento {plano['orcamento_min']} min)")
    print("-" * 78)
    for p in plano["paradas"]:
        print(f"{p['ordem']:>2}. {p['chegada']}  {p['nome']} ({p['estacao']})  "
              f"+{p['deslocamento_min']:.0f} min  rota: {' → '.join(p['rota'])}")
        for s in p["escadas"]:
            print(f"      {s['inicio']}  {s['escada']:<9} criticidade {s['criticidade']:>6.2f}"
                  f"   reparo {s['reparo_min']:>3} min")
    r = plano["retorno"]
    print(f"    {r['chegada']}  retorno à base  +{r['deslocamento_min']:.0f} min  "
          f"rota: {' → '.join(r['rota'])}")
    print("-" * 78)
    print(f"Escadas atendidas: {plano['escadas_atendidas']} | criticidade atendida: "
          f"{plano['criticidade_atendida']} | tempo usado: {plano['tempo_total_min']:.0f} de "
          f"{plano['orcamento_min']} min (deslocamento {plano['tempo_deslocamento_min']:.0f}, "
          f"reparo {plano['tempo_reparo_min']})")
