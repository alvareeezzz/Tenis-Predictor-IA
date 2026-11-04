"""Fase 2 - Calcula el Elo de todos los jugadores partido a partido.

Tres ratings por jugador:
  - general: todos los partidos
  - por superficie: solo los partidos en dura, tierra o hierba
  - combinado: mezcla de los dos anteriores para la superficie del partido (el que mejor predice)

Se calcula con circuito principal, Challengers y previas, pero se evalúa solo en el circuito principal.

Fórmula de FiveThirtyEight: K = 250 / (partidos_jugados + 5) ^ 0.4, de modo que los jugadores
nuevos se mueven rápido y los veteranos despacio. Los Grand Slams pesan un 10 % más.

Salidas:
  data/partidos_elo.parquet  Elo de cada jugador ANTES de cada partido (sin fuga de datos)
  data/elo_actual.parquet    Elo actual de cada jugador
"""

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DATOS = Path(__file__).parent / "data"

ELO_INICIAL = 1500.0
SUPERFICIES = ["Hard", "Clay", "Grass"]
PLUS_GRAND_SLAM = 1.1
# Peso del Elo de superficie en el combinado, elegido con los partidos de 2015-2022
# (mejor log loss); los de 2023 en adelante se reservan para la evaluación.
PESO_SUPERFICIE = 0.4


def factor_k(partidos_jugados: int) -> float:
    return 250 / (partidos_jugados + 5) ** 0.4


def prob_victoria(elo_a: float, elo_b: float) -> float:
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))


def calcular_elo(partidos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    elo = defaultdict(lambda: ELO_INICIAL)
    jugados = defaultdict(int)
    elo_sup = {s: defaultdict(lambda: ELO_INICIAL) for s in SUPERFICIES}
    jugados_sup = {s: defaultdict(int) for s in SUPERFICIES}
    ultimo_partido = {}

    previos = []
    for p in partidos[["winner_id", "loser_id", "superficie_elo", "tourney_level", "final_normal", "fecha"]].itertuples(index=False):
        g, pe, sup = int(p.winner_id), int(p.loser_id), p.superficie_elo
        previos.append((
            elo[g], elo[pe], elo_sup[sup][g], elo_sup[sup][pe],
            jugados[g], jugados[pe], jugados_sup[sup][g], jugados_sup[sup][pe],
        ))
        ultimo_partido[g] = ultimo_partido[pe] = p.fecha

        # Un abandono no dice quién era mejor ese día: se registra el Elo previo pero no se actualiza.
        if not p.final_normal:
            continue

        plus = PLUS_GRAND_SLAM if p.tourney_level == "G" else 1.0
        for ratings, cuenta in ((elo, jugados), (elo_sup[sup], jugados_sup[sup])):
            esperado = prob_victoria(ratings[g], ratings[pe])
            ratings[g] += factor_k(cuenta[g]) * plus * (1 - esperado)
            ratings[pe] -= factor_k(cuenta[pe]) * plus * (1 - esperado)
            cuenta[g] += 1
            cuenta[pe] += 1

    columnas = ["elo_w", "elo_l", "elo_sup_w", "elo_sup_l", "partidos_w", "partidos_l", "partidos_sup_w", "partidos_sup_l"]
    partidos_elo = pd.DataFrame(previos, columns=columnas)
    partidos_elo.insert(0, "orden", partidos["orden"].values)

    actual = pd.DataFrame({
        "player_id": list(elo.keys()),
        "elo": list(elo.values()),
        "partidos": [jugados[j] for j in elo],
        "ultimo_partido": [ultimo_partido[j] for j in elo],
    })
    for s in SUPERFICIES:
        actual[f"elo_{s.lower()}"] = actual["player_id"].map(elo_sup[s]).fillna(ELO_INICIAL)
        actual[f"partidos_{s.lower()}"] = actual["player_id"].map(jugados_sup[s]).fillna(0).astype(int)
    return partidos_elo, actual


def evaluar(partidos: pd.DataFrame, desde: str = "2023-01-01") -> None:
    """Compara, en partidos recientes, cuánto acierta cada Elo frente al ranking ATP."""
    p = partidos[(partidos["fecha"] >= desde) & partidos["final_normal"] & partidos["circuito_principal"]]
    # En los datos el ganador siempre va primero: acertar = darle a él más de un 50 %.
    prob = {
        "Ranking ATP (mejor clasificado gana)": np.where(
            p["winner_rank"].fillna(9999) < p["loser_rank"].fillna(9999), 1.0,
            np.where(p["winner_rank"].fillna(9999) == p["loser_rank"].fillna(9999), 0.5, 0.0)),
        "Elo general": prob_victoria(p["elo_w"], p["elo_l"]),
        "Elo de superficie": prob_victoria(p["elo_sup_w"], p["elo_sup_l"]),
        "Elo combinado": prob_victoria(
            (1 - PESO_SUPERFICIE) * p["elo_w"] + PESO_SUPERFICIE * p["elo_sup_w"],
            (1 - PESO_SUPERFICIE) * p["elo_l"] + PESO_SUPERFICIE * p["elo_sup_l"]),
    }
    print(f"\nEvaluación en {len(p):,} partidos del circuito principal desde {desde}:")
    print(f"  {'Método':<38} {'Acierto':>8} {'Log loss':>9} {'Brier':>7}")
    for nombre, pr in prob.items():
        pr = np.clip(np.asarray(pr, dtype=float), 1e-6, 1 - 1e-6)
        acierto = (pr > 0.5).mean() + 0.5 * (pr == 0.5).mean()
        if nombre.startswith("Ranking"):
            print(f"  {nombre:<38} {acierto:>8.1%} {'-':>9} {'-':>7}")
        else:
            print(f"  {nombre:<38} {acierto:>8.1%} {-np.log(pr).mean():>9.4f} {((1 - pr) ** 2).mean():>7.4f}")


def mostrar_top(actual: pd.DataFrame, fecha_fin: pd.Timestamp) -> None:
    jugadores = pd.read_csv(DATOS / "raw" / "atp_players.csv", usecols=["player_id", "name_first", "name_last"])
    jugadores["nombre"] = jugadores["name_first"].fillna("") + " " + jugadores["name_last"].fillna("")
    activos = actual[actual["ultimo_partido"] >= fecha_fin - pd.Timedelta(days=365)]
    activos = activos.merge(jugadores[["player_id", "nombre"]], on="player_id")
    for col, titulo in [("elo", "general"), ("elo_hard", "pista dura"), ("elo_clay", "tierra"), ("elo_grass", "hierba")]:
        top = activos.nlargest(10, col)
        print(f"\nTop 10 Elo {titulo}:")
        for i, fila in enumerate(top.itertuples(), 1):
            print(f"  {i:>2}. {fila.nombre:<26} {getattr(fila, col):>6.0f}")


def main() -> None:
    partidos = pd.read_parquet(DATOS / "partidos.parquet")
    partidos_elo, actual = calcular_elo(partidos)
    partidos_elo.to_parquet(DATOS / "partidos_elo.parquet", index=False)
    actual.to_parquet(DATOS / "elo_actual.parquet", index=False)
    print(f"Elo calculado para {len(actual):,} jugadores en {len(partidos):,} partidos.")

    evaluar(partidos.merge(partidos_elo, on="orden"))
    mostrar_top(actual, partidos["fecha"].max())


if __name__ == "__main__":
    main()
