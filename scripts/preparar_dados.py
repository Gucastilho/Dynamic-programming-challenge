"""
preparar_dados.py
=================

Converte as exportações brutas do SAP (arquivos ``ecr_AAAA.XLSX``, aba
``ZMM_ANALISE_NOTAS``) nos arquivos limpos usados pelo notebook:

* ``data/eventos_escadas.csv``  – uma linha por nota de manutenção datada
* ``data/escadas.csv``          – cadastro das escadas, com a estação de cada uma

Uso::

    python scripts/preparar_dados.py <pasta_com_os_xlsx>

Os arquivos brutos (≈ 20 MB) não vão para o repositório; os CSVs gerados vão.

Regras de limpeza (todas documentadas no README):

1. Concatena todos os arquivos e remove notas duplicadas pelo número da ``Nota``.
2. Extrai estação (``PIN``, ``LUZ``…) e escada (``ER16``) de ``Local de Instalação``
   (ex.: ``SP04-6-ECR-PIN-ER16``). Linhas sem escada identificada são descartadas.
3. Descarta notas canceladas ou marcadas como duplicadas no status do SAP.
4. Monta início e fim da avaria a partir de data + hora; a duração só é aceita
   entre 0 e 30 dias (acima disso é erro de apontamento e vira vazio).
5. Notas sem data de avaria (quase todas preventivas ``Z3``) não entram nos
   eventos, mas são contadas por escada em ``escadas.csv`` como contexto.
"""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA_DATA = os.path.join(RAIZ, "data")

COLUNAS = {
    "Tipo de Nota": "tipo",
    "Nota": "nota",
    "Indisponível": "indisponivel",
    "Falha": "falha",
    "Descrição": "descricao",
    "Local de Instalação": "local",
    "Status do Usuário da Ordem": "status",
    "Início avaria": "data_ini",
    "Hora início avaria": "hora_ini",
    "Fim da avaria": "data_fim",
    "Hora do fim da avaria": "hora_fim",
    "Defeito": "defeito",
    "Sintoma": "sintoma",
    "Causa": "causa",
    "Ação": "acao",
    "Objeto": "objeto",
}

DURACAO_MAX_MIN = 30 * 24 * 60  # 30 dias


def ler_brutos(pasta: str) -> pd.DataFrame:
    """Lê e concatena todos os ``ecr_*.XLSX`` da pasta (exceto o consolidado ``ecr_09-26``)."""
    arquivos = sorted(
        f for f in glob.glob(os.path.join(pasta, "*ecr_*.XLSX"))
        if "09-26" not in os.path.basename(f)
    )
    if not arquivos:
        raise FileNotFoundError(f"Nenhum ecr_*.XLSX encontrado em {pasta}")
    partes = []
    for f in arquivos:
        df = pd.read_excel(f, dtype=str)
        partes.append(df[list(COLUNAS)].rename(columns=COLUNAS))
        print(f"  {os.path.basename(f):<28} {len(df):>7} linhas")
    return pd.concat(partes, ignore_index=True)


def _data_hora(data: pd.Series, hora: pd.Series) -> pd.Series:
    return pd.to_datetime(data.fillna("") + " " + hora.fillna(""),
                          format="%d/%m/%Y %H:%M:%S", errors="coerce")


def limpar(bruto: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aplica as regras de limpeza e devolve ``(eventos, escadas)``."""
    df = bruto.drop_duplicates("nota").copy()
    n_dedup = len(bruto) - len(df)

    loc = df["local"].fillna("")
    df["estacao"] = loc.str.extract(r"ECR-([A-Z]{3})-")[0]
    num = loc.str.extract(r"-ER(\d+)")[0]
    df["escada"] = df["estacao"] + "-ER" + num.str.zfill(2)
    sem_escada = df["escada"].isna().sum()
    df = df.dropna(subset=["escada"])

    status = df["status"].fillna("")
    canc = status.str.contains("CANC|DUPL")
    df = df[~canc]

    df["inicio"] = _data_hora(df["data_ini"], df["hora_ini"])
    df["fim"] = _data_hora(df["data_fim"], df["hora_fim"])
    dur = (df["fim"] - df["inicio"]).dt.total_seconds() / 60
    df["duracao_min"] = dur.where((dur >= 0) & (dur <= DURACAO_MAX_MIN)).round(1)

    df["indisponivel"] = df["indisponivel"].eq("X").astype(int)
    df["falha"] = df["falha"].eq("X").astype(int)

    # Contexto: preventivas por escada (a maioria não tem data de avaria)
    prev = df[df["tipo"] == "Z3"].groupby("escada").size().rename("n_preventivas")

    eventos = df.dropna(subset=["inicio"]).copy()
    eventos = eventos[[
        "nota", "tipo", "escada", "estacao", "inicio", "fim", "duracao_min",
        "indisponivel", "falha", "descricao", "defeito", "sintoma", "causa",
        "acao", "objeto",
    ]].sort_values(["escada", "inicio"]).reset_index(drop=True)

    escadas = (eventos.groupby(["escada", "estacao"])
               .agg(primeiro_evento=("inicio", "min"),
                    ultimo_evento=("inicio", "max"),
                    n_eventos=("nota", "size"))
               .reset_index())
    escadas = escadas.merge(prev, on="escada", how="left").fillna({"n_preventivas": 0})
    escadas["n_preventivas"] = escadas["n_preventivas"].astype(int)

    print(f"\n  linhas brutas ............ {len(bruto):>7}")
    print(f"  duplicatas removidas ..... {n_dedup:>7}")
    print(f"  sem escada identificada .. {sem_escada:>7}")
    print(f"  canceladas/duplicadas SAP  {int(canc.sum()):>7}")
    print(f"  eventos datados finais ... {len(eventos):>7}")
    print(f"  escadas .................. {len(escadas):>7}")
    return eventos, escadas


def main() -> None:
    pasta = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "data", "raw")
    print(f"Lendo arquivos de {pasta}")
    eventos, escadas = limpar(ler_brutos(pasta))
    os.makedirs(PASTA_DATA, exist_ok=True)
    eventos.to_csv(os.path.join(PASTA_DATA, "eventos_escadas.csv"), index=False)
    escadas.to_csv(os.path.join(PASTA_DATA, "escadas.csv"), index=False)
    print(f"\nArquivos gravados em {PASTA_DATA}")


if __name__ == "__main__":
    main()
