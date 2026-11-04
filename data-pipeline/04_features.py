"""Fase 3 - Características de cada partido: estadísticas, forma, cansancio y enfrentamiento directo.

Cada valor se calcula SOLO con partidos anteriores al partido que se quiere predecir (sin fuga de
datos). Ventanas:
  _52s  últimas 52 semanas, todas las superficies
  _sup  últimos 2 años en la superficie del partido

Los jugadores se colocan como A y B al azar (semilla fija): en los CSV el ganador siempre va
primero y el modelo no debe poder aprender eso.

Salidas:
  data/features.parquet       una fila por partido (desde 1991) con las características de A y B
  data/estadisticas_actuales.parquet  estadísticas de cada jugador a fecha de hoy, para la app
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

DATOS = Path(__file__).parent / "data"

PRIMER_ANIO_FEATURES = 1991  # primer año con estadísticas de saque
DIAS_52S = 365
DIAS_SUPERFICIE = 730
DIAS_H2H_RECIENTE = 1095
MIN_PARTIDOS_CON_STATS = 5  # con menos, los porcentajes son ruido y se dejan vacíos
SEMILLA = 42

ESTADISTICAS = ["ace", "df", "svpt", "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced"]


# --- Marcador -----------------------------------------------------------------------------

PATRON_SET = re.compile(r"(\[?)(\d+)-(\d+)(\(\d+\))?")


def analizar_marcador(marcador: str) -> tuple[int, int, int, int, int, int]:
    """Sets, juegos y tie-breaks ganados por el ganador y por el perdedor del partido."""
    sets_g = sets_p = juegos_g = juegos_p = tb_g = tb_p = 0
    for corchete, a, b, parentesis in PATRON_SET.findall(marcador):
        a, b = int(a), int(b)
        tiebreak = bool(corchete or parentesis) or {a, b} == {6, 7}
        if corchete:  # super tie-break a 10 puntos: cuenta como un juego
            a, b = int(a > b), int(b > a)
        juegos_g += a
        juegos_p += b
        terminado = tiebreak or max(a, b) >= 6 and abs(a - b) >= 2
        if terminado:
            sets_g += a > b
            sets_p += b > a
            if tiebreak:
                tb_g += a > b
                tb_p += b > a
    return sets_g, sets_p, juegos_g, juegos_p, tb_g, tb_p


# --- Tabla por jugador ---------------------------------------------------------------------

def tabla_por_jugador(partidos: pd.DataFrame, elo: pd.DataFrame) -> pd.DataFrame:
    """Dos filas por partido, una desde el punto de vista de cada jugador."""
    elo = elo.set_index("orden").loc[partidos["orden"]].set_axis(partidos.index)
    marcador = pd.DataFrame(
        partidos["score"].map(analizar_marcador).tolist(),
        columns=["sets_g", "sets_p", "juegos_g", "juegos_p", "tb_g", "tb_p"],
        index=partidos.index,
    )
    comunes = partidos[["orden", "fecha", "tourney_id", "superficie_elo", "minutes"]]
    tiene_stats = partidos[[f"{p}_{e}" for p in "wl" for e in ("svpt", "SvGms")]].fillna(0).gt(0).all(axis=1)

    filas = []
    for yo, rival, gana in (("w", "l", 1), ("l", "w", 0)):
        mio, suyo = ("_g", "_p") if gana else ("_p", "_g")
        t = comunes.copy()
        t["jugador"] = partidos[f"{'winner' if gana else 'loser'}_id"].astype(int)
        t["rival"] = partidos[f"{'loser' if gana else 'winner'}_id"].astype(int)
        t["victorias"] = gana
        t["partidos"] = 1
        t["n_stats"] = tiene_stats.astype(int)
        # Nivel del rival: un 45 % de puntos al resto no vale lo mismo en un Challenger que en un Masters.
        t["elo_rival"] = elo[f"elo_{rival}"]
        for e in ESTADISTICAS:
            t[e] = partidos[f"{yo}_{e}"].where(tiene_stats, 0).fillna(0)
            t[f"o_{e}"] = partidos[f"{rival}_{e}"].where(tiene_stats, 0).fillna(0)
        for nombre in ("sets", "juegos", "tb"):
            t[f"{nombre}_g"] = marcador[f"{nombre}{mio}"]
            t[f"{nombre}_p"] = marcador[f"{nombre}{suyo}"]
        filas.append(t)

    largo = pd.concat(filas, ignore_index=True)
    largo["minutes"] = largo["minutes"].fillna(0)
    largo["dia"] = (largo["fecha"] - pd.Timestamp("1960-01-01")).dt.days
    return largo


SUMABLES = ["partidos", "victorias", "n_stats", "elo_rival",*ESTADISTICAS, *[f"o_{e}" for e in ESTADISTICAS],
            "sets_g", "sets_p", "juegos_g", "juegos_p", "tb_g", "tb_p"]


def suma_previa(largo: pd.DataFrame, claves: list[str], columnas: list[str], dias: int | None = None,
                ultimos: int | None = None) -> pd.DataFrame:
    """Suma de `columnas` en los partidos ANTERIORES de cada grupo (`claves`).

    Con `dias` solo cuenta los partidos de los últimos `dias` días; con `ultimos`, los N últimos.
    Vectorizado con sumas acumuladas: suma(filas w..i-1) = acumulado[i] - acumulado[w].
    """
    orden = largo.sort_values([*claves, "orden"], kind="stable").index
    t = largo.loc[orden]
    grupo = t.groupby(claves, sort=False).ngroup().to_numpy()
    n = len(t)
    idx = np.arange(n)
    inicio_grupo = np.searchsorted(grupo, grupo, side="left")

    if dias is not None:
        # Dentro de cada grupo las filas van en orden cronológico: se busca la primera fila
        # cuya fecha cae dentro de la ventana.
        clave = grupo.astype(np.int64) * 1_000_000 + t["dia"].to_numpy()
        desde = np.searchsorted(clave, grupo.astype(np.int64) * 1_000_000 + t["dia"].to_numpy() - dias, side="right")
    elif ultimos is not None:
        desde = np.maximum(inicio_grupo, idx - ultimos)
    else:
        desde = inicio_grupo
    desde = np.minimum(desde, idx)

    valores = t[columnas].to_numpy(dtype=float)
    acumulado = np.vstack([np.zeros(len(columnas)), np.cumsum(valores, axis=0)])
    sumas = acumulado[idx] - acumulado[desde]
    return pd.DataFrame(sumas, columns=columnas, index=orden).loc[largo.index]


def dividir(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return np.divide(a, b, out=np.full_like(a, np.nan), where=b > 0)


def porcentajes(s: pd.DataFrame) -> pd.DataFrame:
    """Convierte sumas de conteos en estadísticas de juego."""
    puntos_saque = s["1stWon"] + s["2ndWon"]
    puntos_resto = s["o_svpt"] - s["o_1stWon"] - s["o_2ndWon"]
    breaks_sufridos = s["bpFaced"] - s["bpSaved"]
    breaks_logrados = s["o_bpFaced"] - s["o_bpSaved"]
    r = pd.DataFrame(index=s.index)
    # Saque
    r["aces_partido"] = dividir(s["ace"], s["n_stats"])
    r["aces_juego"] = dividir(s["ace"], s["SvGms"])
    r["dobles_faltas_partido"] = dividir(s["df"], s["n_stats"])
    r["pct_primer_saque"] = dividir(s["1stIn"], s["svpt"])
    r["pct_ganados_1er_saque"] = dividir(s["1stWon"], s["1stIn"])
    r["pct_ganados_2do_saque"] = dividir(s["2ndWon"], s["svpt"] - s["1stIn"])
    r["pct_puntos_saque"] = dividir(puntos_saque, s["svpt"])
    r["pct_bp_salvados"] = dividir(s["bpSaved"], s["bpFaced"])
    r["pct_juegos_saque"] = 1 - dividir(breaks_sufridos, s["SvGms"])
    # Resto (sale de las estadísticas de saque del rival)
    r["aces_recibidos_partido"] = dividir(s["o_ace"], s["n_stats"])
    r["pct_resto_1er_saque"] = dividir(s["o_1stIn"] - s["o_1stWon"], s["o_1stIn"])
    r["pct_resto_2do_saque"] = dividir(s["o_svpt"] - s["o_1stIn"] - s["o_2ndWon"], s["o_svpt"] - s["o_1stIn"])
    r["pct_puntos_resto"] = dividir(puntos_resto, s["o_svpt"])
    r["pct_bp_convertidos"] = dividir(breaks_logrados, s["o_bpFaced"])
    r["pct_juegos_resto"] = dividir(breaks_logrados, s["o_SvGms"])
    # Globales
    r["pct_puntos_totales"] = dividir(puntos_saque + puntos_resto, s["svpt"] + s["o_svpt"])
    r["dominance_ratio"] = dividir(r["pct_puntos_resto"], 1 - r["pct_puntos_saque"])
    pocas = s["n_stats"] < MIN_PARTIDOS_CON_STATS
    r.loc[pocas, :] = np.nan
    # Resultados (no dependen de tener estadísticas)
    r["pct_victorias"] = dividir(s["victorias"], s["partidos"])
    r["elo_medio_rivales"] = dividir(s["elo_rival"], s["partidos"])
    r["pct_sets"] = dividir(s["sets_g"], s["sets_g"] + s["sets_p"])
    r["pct_juegos"] = dividir(s["juegos_g"], s["juegos_g"] + s["juegos_p"])
    r["pct_tiebreaks"] = dividir(s["tb_g"], s["tb_g"] + s["tb_p"])
    r["tiebreaks_jugados"] = s["tb_g"] + s["tb_p"]
    r["partidos"] = s["partidos"]
    r["partidos_con_stats"] = s["n_stats"]
    return r


def features_por_jugador(largo: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=largo.index)
    f[["orden", "jugador", "rival"]] = largo[["orden", "jugador", "rival"]]

    # Estadísticas: últimas 52 semanas y superficie del partido en los últimos 2 años
    f = f.join(porcentajes(suma_previa(largo, ["jugador"], SUMABLES, dias=DIAS_52S)).add_suffix("_52s"))
    f = f.join(porcentajes(suma_previa(largo, ["jugador", "superficie_elo"], SUMABLES, dias=DIAS_SUPERFICIE)).add_suffix("_sup"))

    # Forma
    for n in (10, 20):
        s = suma_previa(largo, ["jugador"], ["partidos", "victorias"], ultimos=n)
        f[f"victorias_ult{n}"] = dividir(s["victorias"], s["partidos"])

    # Cansancio: lo jugado en las dos últimas semanas y en este mismo torneo
    s = suma_previa(largo, ["jugador"], ["partidos", "minutes"], dias=14)
    f["partidos_14d"], f["minutos_14d"] = s["partidos"], s["minutes"]
    f["minutos_torneo"] = suma_previa(largo, ["jugador", "tourney_id"], ["minutes"])["minutes"]
    # Sackmann solo da la fecha de inicio del torneo: dentro de un mismo torneo esto vale 0.
    previo = largo.sort_values(["jugador", "orden"]).groupby("jugador")["dia"].shift()
    f["dias_desde_ultimo_torneo"] = (largo["dia"] - previo).clip(upper=365)

    # Enfrentamiento directo (desde el punto de vista de este jugador)
    for sufijo, claves, dias in (("", ["jugador", "rival"], None),
                                 ("_sup", ["jugador", "rival", "superficie_elo"], None),
                                 ("_3a", ["jugador", "rival"], DIAS_H2H_RECIENTE)):
        s = suma_previa(largo, claves, ["partidos", "victorias", "sets_g", "sets_p"], dias=dias)
        f[f"h2h{sufijo}_partidos"] = s["partidos"]
        f[f"h2h{sufijo}_victorias"] = s["victorias"]
        if not sufijo:
            f["h2h_pct_sets"] = dividir(s["sets_g"], s["sets_g"] + s["sets_p"])
    return f


# --- Tabla final A contra B ------------------------------------------------------------------

def construir_features(partidos: pd.DataFrame, elo: pd.DataFrame, por_jugador: pd.DataFrame) -> pd.DataFrame:
    p = partidos.merge(elo, on="orden")
    p = p[p["fecha"].dt.year >= PRIMER_ANIO_FEATURES].reset_index(drop=True)

    rng = np.random.default_rng(SEMILLA)
    gana_a = rng.random(len(p)) < 0.5

    def lado(ganador: str, perdedor: str):
        return np.where(gana_a, p[ganador], p[perdedor])

    t = p[["orden", "fecha", "tourney_id", "tourney_name", "tourney_level", "surface", "superficie_elo",
           "round", "best_of", "circuito_principal", "final_normal"]].copy()
    t["gana_a"] = gana_a.astype(int)
    columnas_jugador = {
        "id": ("winner_id", "loser_id"), "nombre": ("winner_name", "loser_name"),
        "rank": ("winner_rank", "loser_rank"), "puntos_rank": ("winner_rank_points", "loser_rank_points"),
        "edad": ("winner_age", "loser_age"), "altura": ("winner_ht", "loser_ht"),
        "zurdo": ("winner_hand", "loser_hand"), "entrada": ("winner_entry", "loser_entry"),
        "elo": ("elo_w", "elo_l"), "elo_sup": ("elo_sup_w", "elo_sup_l"),
        "partidos_elo": ("partidos_w", "partidos_l"), "partidos_elo_sup": ("partidos_sup_w", "partidos_sup_l"),
    }
    for nombre, (g, pe) in columnas_jugador.items():
        t[f"{nombre}_a"] = lado(g, pe)
        t[f"{nombre}_b"] = lado(pe, g)
    for s in "ab":
        t[f"id_{s}"] = t[f"id_{s}"].astype(int)
        t[f"zurdo_{s}"] = (t[f"zurdo_{s}"] == "L").astype(int)
        t[f"viene_de_previa_{s}"] = t[f"entrada_{s}"].isin(["Q", "LL"]).astype(int)
        t = t.drop(columns=f"entrada_{s}")

    # Pegar las características de cada jugador para ese partido
    pj = por_jugador.drop(columns="rival").set_index(["orden", "jugador"])
    for s in "ab":
        claves = pd.MultiIndex.from_arrays([t["orden"], t[f"id_{s}"]])
        t = pd.concat([t, pj.reindex(claves).add_suffix(f"_{s}").reset_index(drop=True)], axis=1)
    return t


# --- Estadísticas actuales (para la app) ------------------------------------------------------

def estadisticas_actuales(largo: pd.DataFrame) -> pd.DataFrame:
    hoy = largo["dia"].max() + 1
    bloques = []
    for nombre, filtro, claves in (
        ("52s", largo["dia"] > hoy - DIAS_52S, ["jugador"]),
        ("sup", largo["dia"] > hoy - DIAS_SUPERFICIE, ["jugador", "superficie_elo"]),
        ("carrera", slice(None), ["jugador"]),
    ):
        s = largo.loc[filtro].groupby(claves)[SUMABLES].sum()
        r = porcentajes(s).reset_index()
        if "superficie_elo" not in r:
            r["superficie_elo"] = "Todas"
        r["ventana"] = nombre
        bloques.append(r)
    return pd.concat(bloques, ignore_index=True).rename(columns={"jugador": "player_id", "superficie_elo": "superficie"})


# --- Comprobación de que no hay fuga de datos -------------------------------------------------

def verificar_sin_fuga(largo: pd.DataFrame, por_jugador: pd.DataFrame, muestras: int = 300) -> None:
    """Recalcula a mano, con filtros simples, unas cuantas características y las compara."""
    rng = np.random.default_rng(0)
    candidatas = largo.index[largo["fecha"].dt.year >= 2015]
    errores = 0
    por_jugador_largo = largo.groupby("jugador")
    for i in rng.choice(candidatas, muestras, replace=False):
        fila = largo.loc[i]
        suyos = por_jugador_largo.get_group(fila["jugador"])
        previos = suyos[suyos["orden"] < fila["orden"]].sort_values("orden")
        v52 = previos[previos["dia"] > fila["dia"] - DIAS_52S]
        esperado = {
            "aces_juego_52s": v52["ace"].sum() / v52["SvGms"].sum() if v52["n_stats"].sum() >= MIN_PARTIDOS_CON_STATS else np.nan,
            "pct_victorias_52s": v52["victorias"].mean() if len(v52) else np.nan,
            "h2h_victorias": previos.loc[previos["rival"] == fila["rival"], "victorias"].sum(),
            "victorias_ult10": previos["victorias"].tail(10).mean() if len(previos) else np.nan,
        }
        for col, valor in esperado.items():
            calculado = por_jugador.at[i, col]
            if not (np.isclose(calculado, valor) or (np.isnan(calculado) and np.isnan(valor))):
                errores += 1
                print(f"  ✗ fila {i} {col}: calculado {calculado}, esperado {valor}")
    print(f"Comprobación sin fuga de datos: {muestras * 4 - errores}/{muestras * 4} valores correctos")
    if errores:
        raise SystemExit("Hay diferencias: revisa el cálculo de las ventanas.")


def main() -> None:
    partidos = pd.read_parquet(DATOS / "partidos.parquet")
    elo = pd.read_parquet(DATOS / "partidos_elo.parquet")

    largo = tabla_por_jugador(partidos, elo)
    por_jugador = features_por_jugador(largo)
    verificar_sin_fuga(largo, por_jugador)

    features = construir_features(partidos, elo, por_jugador)
    features.to_parquet(DATOS / "features.parquet", index=False)
    print(f"features.parquet: {len(features):,} partidos x {features.shape[1]} columnas "
          f"({features['circuito_principal'].sum():,} del circuito principal)")

    actuales = estadisticas_actuales(largo)
    actuales.to_parquet(DATOS / "estadisticas_actuales.parquet", index=False)
    print(f"estadisticas_actuales.parquet: {actuales['player_id'].nunique():,} jugadores")


if __name__ == "__main__":
    main()
