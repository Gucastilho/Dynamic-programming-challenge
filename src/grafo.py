"""
grafo.py
========

Rede de estações como grafo ponderado (lista de adjacência) e os algoritmos de
caminho:

* :func:`bfs`       – menor número de trechos (ignora os pesos)
* :func:`dijkstra`  – menor tempo total (pesos em minutos)
* :func:`a_estrela` – menor tempo total, guiado por heurística geográfica
* :func:`reconstruir_rota` – refaz o caminho a partir do dicionário de predecessores

Todos os algoritmos devolvem também o número de vértices **expandidos**
(retirados da fila), que é a medida usada para comparar Dijkstra e A*.
"""
from __future__ import annotations

import heapq
import json
import math
from collections import deque
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Grafo:
    """Grafo não direcionado e ponderado guardado como lista de adjacência.

    Attributes:
        adj: ``{vértice: [(vizinho, peso_min, modo), …]}``.
        coords: ``{vértice: (lat, lon)}`` – usado pela heurística do A*.
        nomes: ``{vértice: nome legível}``.
        escadas: ``{estação: [escadas ligadas a ela]}``.
        base: id do vértice da base de manutenção.
    """
    adj: dict = field(default_factory=dict)
    coords: dict = field(default_factory=dict)
    nomes: dict = field(default_factory=dict)
    escadas: dict = field(default_factory=dict)
    base: str = "BASE"

    def adicionar_vertice(self, v: str, lat: float, lon: float, nome: str = "") -> None:
        """Cria o vértice ``v`` (se ainda não existe) com suas coordenadas."""
        self.adj.setdefault(v, [])
        self.coords[v] = (lat, lon)
        self.nomes[v] = nome or v

    def adicionar_aresta(self, u: str, v: str, peso: float, modo: str = "") -> None:
        """Liga ``u`` e ``v`` nos dois sentidos com peso em minutos."""
        self.adj[u].append((v, peso, modo))
        self.adj[v].append((u, peso, modo))

    def vizinhos(self, v: str):
        """Itera sobre ``(vizinho, peso)`` de ``v``."""
        for w, p, _ in self.adj[v]:
            yield w, p

    def peso(self, u: str, v: str) -> float:
        """Peso da aresta ``u–v`` (menor, se houver mais de uma)."""
        return min(p for w, p, _ in self.adj[u] if w == v)

    def arestas(self):
        """Lista única de arestas ``(u, v, peso, modo)``."""
        vistas, saida = set(), []
        for u, lst in self.adj.items():
            for v, p, m in lst:
                chave = tuple(sorted((u, v))) + (p,)
                if chave not in vistas:
                    vistas.add(chave)
                    saida.append((u, v, p, m))
        return saida


def carregar_rede(caminho_json: str, escadas: pd.DataFrame | None = None) -> Grafo:
    """Lê ``rede.json`` e monta o :class:`Grafo`.

    Se ``escadas`` (com colunas ``escada`` e ``estacao``) for informado, cada
    escada é ligada à sua estação em ``grafo.escadas``. Uma estação do cadastro
    que não exista na rede gera ``ValueError``.
    """
    with open(caminho_json, encoding="utf-8") as f:
        rede = json.load(f)
    g = Grafo(base=rede["base"])
    for v in rede["vertices"]:
        g.adicionar_vertice(v["id"], v["lat"], v["lon"], v["nome"])
    for e in rede["arestas"]:
        g.adicionar_aresta(e["de"], e["para"], e["minutos"], e.get("modo", ""))
    if escadas is not None:
        faltando = set(escadas["estacao"]) - set(g.adj)
        if faltando:
            raise ValueError(f"Estações sem vértice na rede: {faltando}")
        for est, grupo in escadas.groupby("estacao"):
            g.escadas[est] = sorted(grupo["escada"])
    return g


# --------------------------------------------------------------------------- #
# Reconstrução de rota
# --------------------------------------------------------------------------- #
def reconstruir_rota(pred: dict, origem: str, destino: str) -> list[str]:
    """Refaz o caminho ``origem → destino`` seguindo os predecessores.

    Returns:
        Lista de vértices do caminho, ou ``[]`` se o destino é inalcançável.
    """
    if destino != origem and destino not in pred:
        return []
    rota = [destino]
    while rota[-1] != origem:
        rota.append(pred[rota[-1]])
    return rota[::-1]


def custo_rota(g: Grafo, rota: list[str]) -> float:
    """Soma dos pesos (minutos) ao longo de uma rota."""
    return sum(g.peso(u, v) for u, v in zip(rota, rota[1:]))


# --------------------------------------------------------------------------- #
# BFS
# --------------------------------------------------------------------------- #
def bfs(g: Grafo, origem: str) -> tuple[dict, dict, int]:
    """Busca em largura a partir de ``origem``.

    Encontra, para cada vértice, o caminho com **menos trechos** (arestas),
    sem olhar para os minutos.

    Returns:
        ``(saltos, pred, expandidos)`` – nº de trechos até cada vértice,
        predecessores para :func:`reconstruir_rota` e vértices retirados da fila.
    """
    saltos, pred = {origem: 0}, {}
    fila, expandidos = deque([origem]), 0
    while fila:
        u = fila.popleft()
        expandidos += 1
        for v, _ in g.vizinhos(u):
            if v not in saltos:
                saltos[v] = saltos[u] + 1
                pred[v] = u
                fila.append(v)
    return saltos, pred, expandidos


# --------------------------------------------------------------------------- #
# Dijkstra
# --------------------------------------------------------------------------- #
def dijkstra(g: Grafo, origem: str, destino: str | None = None) -> tuple[dict, dict, int]:
    """Dijkstra com fila de prioridade (heap).

    Args:
        origem: vértice de partida.
        destino: se informado, para assim que o destino sai da fila (versão
            ponto a ponto, comparável ao A*). Sem destino, calcula a árvore de
            menores tempos para todos os vértices.

    Returns:
        ``(dist, pred, expandidos)`` – minutos até cada vértice, predecessores e
        número de vértices expandidos.
    """
    dist, pred = {origem: 0.0}, {}
    heap, fechados, expandidos = [(0.0, origem)], set(), 0
    while heap:
        d, u = heapq.heappop(heap)
        if u in fechados:
            continue
        fechados.add(u)
        expandidos += 1
        if u == destino:
            break
        for v, w in g.vizinhos(u):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v], pred[v] = nd, u
                heapq.heappush(heap, (nd, v))
    return dist, pred, expandidos


# --------------------------------------------------------------------------- #
# A*
# --------------------------------------------------------------------------- #
def haversine_km(p: tuple, q: tuple) -> float:
    """Distância em linha reta (km) entre dois pontos ``(lat, lon)``."""
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (*p, *q))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def velocidade_maxima(g: Grafo) -> float:
    """Maior velocidade em linha reta observada numa aresta (km/min).

    ``v_max = max(distância_reta(u, v) / minutos(u, v))``. Dividir a distância
    reta por essa velocidade nunca supera o tempo real de nenhum trecho – é o
    que torna a heurística admissível.
    """
    return max(haversine_km(g.coords[u], g.coords[v]) / p for u, v, p, _ in g.arestas())


def heuristica_tempo(g: Grafo, destino: str, v_max: float | None = None):
    """Cria ``h(n) = distância_reta(n, destino) / v_max`` (minutos).

    *Admissível*: a distância percorrida por qualquer caminho é ≥ a linha reta e
    nenhum trecho é mais rápido que ``v_max``, então ``h`` nunca superestima.
    *Consistente*: pela desigualdade triangular,
    ``h(u) ≤ reta(u,v)/v_max + h(v) ≤ w(u,v) + h(v)``.
    """
    v_max = v_max or velocidade_maxima(g)
    alvo = g.coords[destino]
    return lambda n: haversine_km(g.coords[n], alvo) / v_max


def a_estrela(g: Grafo, origem: str, destino: str, h=None) -> tuple[dict, dict, int]:
    """Busca A* de ``origem`` a ``destino``.

    Ordena a fila por ``f(n) = g(n) + h(n)``. Com ``h`` consistente, o primeiro
    momento em que o destino sai da fila já dá o caminho ótimo (mesmo custo do
    Dijkstra), normalmente expandindo menos vértices.

    Args:
        h: função heurística; padrão :func:`heuristica_tempo`.

    Returns:
        ``(dist, pred, expandidos)`` como em :func:`dijkstra`.
    """
    h = h or heuristica_tempo(g, destino)
    dist, pred = {origem: 0.0}, {}
    heap, fechados, expandidos = [(h(origem), 0.0, origem)], set(), 0
    while heap:
        _, d, u = heapq.heappop(heap)
        if u in fechados:
            continue
        fechados.add(u)
        expandidos += 1
        if u == destino:
            break
        for v, w in g.vizinhos(u):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v], pred[v] = nd, u
                heapq.heappush(heap, (nd + h(v), nd, v))
    return dist, pred, expandidos


# --------------------------------------------------------------------------- #
# Grafo sintético (para medir o A* em escala)
# --------------------------------------------------------------------------- #
def grade_sintetica(lado: int = 60, semente: int = 42) -> Grafo:
    """Grade ``lado × lado`` com coordenadas e pesos realistas.

    Vértices espaçados ~0,5 km, pesos = distância / 0,5 km/min × fator
    aleatório entre 1 e 1,6 (trânsito). Serve só para mostrar como o A* se
    comporta quando o grafo é grande.
    """
    import random
    rnd = random.Random(semente)
    g = Grafo()
    passo = 0.0045  # ~0,5 km em graus
    for i in range(lado):
        for j in range(lado):
            g.adicionar_vertice(f"{i},{j}", -23.5 - i * passo, -46.6 - j * passo)
    for i in range(lado):
        for j in range(lado):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < lado and j + dj < lado:
                    u, v = f"{i},{j}", f"{i + di},{j + dj}"
                    km = haversine_km(g.coords[u], g.coords[v])
                    g.adicionar_aresta(u, v, round(km / 0.5 * rnd.uniform(1.0, 1.6), 3))
    return g
