"""Fase 2 - Calcula el Elo de todos los jugadores partido a partido.

Tres ratings por jugador:
  - general: todos los partidos
  - por superficie: solo los partidos en dura, tierra o hierba
  - combinado: mezcla de los dos anteriores para la superficie del partido (el que mejor predice)

Se calcula con circuito principal, Challengers y previas, pero se evalúa solo en el circuito principal.

Fórmula de FiveThirtyEight, K = A / (partidos_jugados + B) ^ C, de modo que los jugadores nuevos se
mueven rápido y los veteranos despacio, con tres añadidos:
  - marcador: ganar 6-1 6-2 mueve más el Elo que ganar 7-6 en el tercero
  - inactividad: tras más de DIAS_AUSENCIA sin jugar el K vuelve a ser el de alguien con menos partidos,
    porque no sabemos cómo vuelve
  - peso de la superficie en el combinado, distinto en dura, tierra y hierba
Los parámetros se eligieron con Optuna (mejor log loss en 2015-2022); los partidos de 2023 en
adelante se reservan para la evaluación.

Salidas:
  data/partidos_elo.parquet  Elo de cada jugador ANTES de cada partido (sin fuga de datos)
  data/elo_actual.parquet    Elo actual de cada jugador
"""

import importlib.util
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DATOS = Path(__file__).parent / "data"

ELO_INICIAL = 1500.0
SUPERFICIES = ["Hard", "Clay", "Grass"]
K_A, K_B, K_C = 225.0, 2.2, 0.45
K_SUPERFICIE = 0.77     # el Elo de superficie se mueve algo más despacio que el general
PLUS_GRAND_SLAM = 0.84  # los Grand Slams pesan menos: al mejor de 5 la sorpresa es más rara y dice menos
EXPONENTE_MARCADOR = 0.49  # K × (2 × proporción de juegos del ganador) ^ EXPONENTE_MARCADOR
DIAS_AUSENCIA = 41
FACTOR_AUSENCIA = 0.74  # tras una ausencia, partidos_jugados × FACTOR_AUSENCIA
# Peso del Elo de superficie en el combinado
PESO_SUPERFICIE = {"Hard": 0.4, "Clay": 0.45, "Grass": 0.35}


def factor_k(partidos_jugados: int) -> float:
    return K_A / (partidos_jugados + K_B) ** K_C


def elo_combinado(elo: pd.Series, elo_sup: pd.Series, superficie: pd.Series) -> pd.Series:
    peso = superficie.map(PESO_SUPERFICIE)
    return (1 - peso) * elo + peso * elo_sup


def prob_victoria(elo_a: float, elo_b: float) -> float:
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))


def proporcion_juegos_ganador(partidos: pd.DataFrame) -> np.ndarray:
    spec = importlib.util.spec_from_file_location("features", Path(__file__).parent / "04_features.py")
    features = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(features)
    m = np.array(partidos["score"].fillna("").map(lambda s: features.analizar_marcador(s)[2:4]).tolist(), dtype=float)
    total = m.sum(axis=1)
    return np.divide(m[:, 0], total, out=np.full(len(m), 0.5), where=total > 0)


def calcular_elo(partidos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    proporcion = proporcion_juegos_ganador(partidos)
    elo = defaultdict(lambda: ELO_INICIAL)
    jugados = defaultdict(int)
    elo_sup = {s: defaultdict(lambda: ELO_INICIAL) for s in SUPERFICIES}
    jugados_sup = {s: defaultdict(int) for s in SUPERFICIES}
    ultimo_partido = {}

    previos = []
    columnas = ["winner_id", "loser_id", "superficie_elo", "tourney_level", "final_normal", "fecha"]
    for p, juegos in zip(partidos[columnas].itertuples(index=False), proporcion):
        g, pe, sup = int(p.winner_id), int(p.loser_id), p.superficie_elo
        for j in (g, pe):
            if j in ultimo_partido and (p.fecha - ultimo_partido[j]).days > DIAS_AUSENCIA:
                jugados[j] = int(jugados[j] * FACTOR_AUSENCIA)
                jugados_sup[sup][j] = int(jugados_sup[sup][j] * FACTOR_AUSENCIA)
        previos.append((
            elo[g], elo[pe], elo_sup[sup][g], elo_sup[sup][pe],
            jugados[g], jugados[pe], jugados_sup[sup][g], jugados_sup[sup][pe],
        ))
        ultimo_partido[g] = ultimo_partido[pe] = p.fecha

        # Un abandono no dice quién era mejor ese día: se registra el Elo previo pero no se actualiza.
        if not p.final_normal:
            continue

        plus = (PLUS_GRAND_SLAM if p.tourney_level == "G" else 1.0) * (2 * juegos) ** EXPONENTE_MARCADOR
        for ratings, cuenta, k in ((elo, jugados, 1.0), (elo_sup[sup], jugados_sup[sup], K_SUPERFICIE)):
            esperado = prob_victoria(ratings[g], ratings[pe])
            ratings[g] += k * factor_k(cuenta[g]) * plus * (1 - esperado)
            ratings[pe] -= k * factor_k(cuenta[pe]) * plus * (1 - esperado)
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
        "Elo combinado": prob_victoria(elo_combinado(p["elo_w"], p["elo_sup_w"], p["superficie_elo"]),
                                       elo_combinado(p["elo_l"], p["elo_sup_l"], p["superficie_elo"])),
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
