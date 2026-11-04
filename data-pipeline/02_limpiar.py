"""Fase 1 - Une y limpia los partidos ATP en una sola tabla ordenada cronológicamente.

Incluye circuito principal, Challengers y previas. Salida: data/partidos.parquet, un partido
por fila con las columnas originales de Sackmann más `fecha`, `orden` (posición cronológica),
`superficie_elo`, `final_normal` y `circuito_principal` (cuadro final de un torneo ATP).
"""

import glob
from pathlib import Path

import pandas as pd

DATOS = Path(__file__).parent / "data"

# Sackmann solo guarda la fecha de inicio del torneo: dentro de un torneo el orden lo da la ronda.
ORDEN_RONDAS = {"Q1": -4, "Q2": -3, "Q3": -2, "Q4": -1, "ER": 0, "RR": 1, "R128": 2, "R64": 3, "R32": 4, "R16": 5, "QF": 6, "SF": 7, "BR": 8, "F": 9}


def cargar_partidos() -> pd.DataFrame:
    archivos = sorted(glob.glob(str(DATOS / "raw" / "atp_matches_[12]*.csv")))
    archivos += sorted(glob.glob(str(DATOS / "raw" / "atp_matches_qual_chall_*.csv")))
    return pd.concat((pd.read_csv(a, low_memory=False) for a in archivos), ignore_index=True)


def limpiar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["winner_id", "loser_id", "score"])
    df = df.drop_duplicates(subset=["tourney_id", "winner_id", "loser_id", "round"]).copy()

    # Un walkover (W/O) no se juega: no aporta información sobre el nivel de nadie.
    df = df[~df["score"].str.contains("W/O|Walkover", case=False)]

    df["fecha"] = pd.to_datetime(df["tourney_date"].astype(int).astype(str), format="%Y%m%d")
    df["ronda_orden"] = df["round"].map(ORDEN_RONDAS).fillna(0).astype(int)
    df["circuito_principal"] = (df["tourney_level"] != "C") & ~df["round"].isin(["Q1", "Q2", "Q3", "Q4"])

    # La moqueta (Carpet) desapareció en 2009; era pista rápida bajo techo, así que su Elo
    # por superficie se acumula con el de pista dura.
    df["superficie_elo"] = df["surface"].replace({"Carpet": "Hard"}).fillna("Hard")

    # Partido terminado con normalidad (sin abandono ni descalificación).
    df["final_normal"] = ~df["score"].str.contains("RET|DEF|Played|unfinished", case=False)

    df = df.sort_values(["fecha", "tourney_id", "ronda_orden", "match_num"], kind="stable")
    df = df.reset_index(drop=True)
    df["orden"] = df.index
    return df


def main() -> None:
    df = limpiar(cargar_partidos())
    df.to_parquet(DATOS / "partidos.parquet", index=False)
    print(f"{len(df):,} partidos | {df['fecha'].min():%Y-%m-%d} → {df['fecha'].max():%Y-%m-%d}")
    print(f"Circuito principal: {df['circuito_principal'].sum():,} | Challengers y previas: {(~df['circuito_principal']).sum():,}")
    print(f"Con estadísticas: {df['w_ace'].notna().sum():,} | Abandonos: {(~df['final_normal']).sum():,}")


if __name__ == "__main__":
    main()
