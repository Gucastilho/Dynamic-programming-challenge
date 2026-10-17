"""
sinais.py
=========

Liga as notas de manutenção do SAP aos **sinais monitorados** citados no desafio
(vibração, temperatura, corrente/tensão, nível de óleo e velocidade) e aos itens
que o desafio declara **fora do escopo dos sensores** (freio, corrimão,
degraus/roletes e trincas estruturais).

Por que isso é necessário
-------------------------
O dataset fornecido pela Motiva é o histórico de notas do SAP, não telemetria.
Cada nota, porém, descreve o sintoma observado (``ALARME-TERMICO DO MOTOR``,
``RUIDO ANORMAL``, ``VAZAMENTO``…). Classificar cada nota na família de sinal
correspondente mostra *qual sensor teria detectado aquele problema* e quanto do
histórico fica fora do alcance dos sensores propostos.

Regra de classificação
----------------------
Os campos são lidos em ordem de evidência – ``sintoma`` → ``defeito`` →
``causa`` → ``objeto`` → ``descricao`` – e a nota recebe a primeira família cujas
palavras-chave aparecem. O sintoma vem primeiro porque é o que um sensor
observaria. Notas sem nenhuma palavra-chave ficam em ``outros``.
"""
from __future__ import annotations

import pandas as pd

# Ordem importa: itens fora do escopo vêm antes para que, por exemplo,
# "ALARME-VELOCIDADE DE CORRIMAO" seja corrimão, e não velocidade dos degraus.
FAMILIAS: dict[str, tuple[str, ...]] = {
    # ---- fora do escopo dos sensores (segundo o desafio) ----
    "freio":            ("FREIO",),
    "corrimao":         ("CORRIM",),
    "degraus_roletes":  ("DEGRAU", "ROLETE"),
    "estrutura":        ("TRINC", "DEFORMACAO", "RUPTURA", "EMPENO", "INFILTRACAO"),
    # ---- sinais monitorados pelos sensores ----
    "temperatura":      ("TERMIC", "SUPERAQUEC", "QUEIMAD", "TEMPERATURA", "AQUEC", "INCENDIO"),
    "nivel_oleo":       ("LUBRIFIC", "OLEO", "ÓLEO", "GRAXA", "VAZAMENTO"),
    "velocidade":       ("VELOC", "TRAVAD", "TRAVAMENTO", "EMPERR", "VARIADOR"),
    "corrente_tensao":  ("SUBTENS", "ALIMENTACAO ELETRICA", "ALIM. ELETRICA", "ENERGIA ELETRICA",
                         "CURTO", "FASE", "ISOLA", "INVERSOR", "DISJUNTOR", "CONTATOR",
                         "FUGA DE CORRENTE", "MOTOR ELETRICO", "NAO LIGA", "MAU CONTATO",
                         "CABEAMENTO", "CABO ELETRICO"),
    "vibracao":         ("RUIDO", "VIBRA", "FOLGA", "ALINHAMENTO", "DESALINH", "ROLAMENTO",
                         "REDUTOR", "CORRENTE PRINCIPAL", "CARRO TEN", "SOLTO", "DESAPERTADO"),
}

SENSORES = ("vibracao", "temperatura", "corrente_tensao", "nivel_oleo", "velocidade")
FORA_DO_ESCOPO = ("freio", "corrimao", "degraus_roletes", "estrutura")
ROTULOS = {
    "vibracao": "Vibração", "temperatura": "Temperatura", "corrente_tensao": "Corrente/Tensão",
    "nivel_oleo": "Nível de óleo", "velocidade": "Velocidade", "freio": "Freio",
    "corrimao": "Corrimão", "degraus_roletes": "Degraus/roletes", "estrutura": "Estrutura/trincas",
    "outros": "Outros (segurança, sensores, iluminação…)",
}
CAMPOS = ("sintoma", "defeito", "causa", "objeto", "descricao")
ILUMINACAO = ("LAMPADA", "LÂMPADA", "LUMINARIA", "REATOR", "ILUMINACAO")


def familia_do_texto(texto: str | float) -> str | None:
    """Família de sinal cujas palavras-chave aparecem em ``texto`` (ou ``None``)."""
    if not isinstance(texto, str):
        return None
    t = texto.upper()
    for familia, chaves in FAMILIAS.items():
        if any(c in t for c in chaves):
            return familia
    return None


def classificar_nota(nota: dict | pd.Series) -> str:
    """Família de sinal de uma nota, lendo os campos em ordem de evidência.

    Args:
        nota: registro com as colunas ``sintoma``, ``defeito``, ``causa``,
            ``objeto`` e ``descricao``.

    Returns:
        Uma das chaves de :data:`FAMILIAS` ou ``"outros"``.
    """
    # Iluminação (lâmpada queimada etc.) não é sinal de desgaste do equipamento:
    # sem esta regra, "QUEIMADO" seria lido como temperatura.
    if any(isinstance(nota.get(c), str) and any(k in nota.get(c).upper() for k in ILUMINACAO)
           for c in CAMPOS):
        return "outros"
    for campo in CAMPOS:
        f = familia_do_texto(nota.get(campo))
        if f:
            return f
    return "outros"


def adicionar_sinal(eventos: pd.DataFrame) -> pd.DataFrame:
    """Devolve uma cópia de ``eventos`` com as colunas ``sinal`` e ``grupo_sinal``.

    ``grupo_sinal`` é ``"sensor"`` (um dos cinco sinais monitorados),
    ``"fora do escopo"`` (freio, corrimão, degraus/roletes, estrutura) ou
    ``"outros"``.
    """
    ev = eventos.copy()
    ev["sinal"] = [classificar_nota(r) for r in ev[list(CAMPOS)].to_dict("records")]
    ev["grupo_sinal"] = ev["sinal"].map(
        lambda s: "sensor" if s in SENSORES else ("fora do escopo" if s in FORA_DO_ESCOPO else "outros"))
    return ev


def sinal_predominante(eventos: pd.DataFrame, meses: int = 12) -> pd.Series:
    """Família de sinal mais frequente de cada escada nos últimos ``meses`` meses.

    Considera só eventos de desgaste (``dano > 0``) e ignora ``outros``.
    """
    ev = eventos.copy()
    ev["inicio"] = pd.to_datetime(ev["inicio"])
    corte = ev["inicio"].max() - pd.DateOffset(months=meses)
    rec = ev[(ev["inicio"] > corte) & (ev["dano"] > 0) & (ev["sinal"] != "outros")]
    return (rec.groupby(["escada", "sinal"]).size().rename("n").reset_index()
               .sort_values(["escada", "n"], ascending=[True, False])
               .drop_duplicates("escada").set_index("escada")["sinal"].map(ROTULOS))
