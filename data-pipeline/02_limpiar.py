"""Fase 1 - Une y limpia los partidos ATP en una sola tabla ordenada cronológicamente.

Incluye circuito principal, Challengers y previas: el archivo de Sackmann (hasta junio de 2026)
más los resultados posteriores de Valuebetennis. Salida: data/partidos.parquet, un partido por
fila con las columnas originales de Sackmann más `fecha`, `orden` (posición cronológica),
`superficie_elo`, `final_normal`, `circuito_principal` (cuadro final de un torneo ATP) y `fuente`.
"""

import glob
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

DATOS = Path(__file__).parent / "data"

# Sackmann solo guarda la fecha de inicio del torneo: dentro de un torneo el orden lo da la ronda.
ORDEN_RONDAS = {"Q1": -4, "Q2": -3, "Q3": -2, "Q4": -1, "ER": 0, "RR": 1, "R128": 2, "R64": 3, "R32": 4, "R16": 5, "QF": 6, "SF": 7, "BR": 8, "F": 9}

# Categorías de Valuebetennis → nivel de torneo de Sackmann. Los ITF no están en el archivo de
# Sackmann (ni en el historial del Elo), así que se dejan fuera.
NIVEL_RECIENTES = {"Grand Chelem": "G", "Masters": "M", "Main tour": "A", "Challenger": "C", "Coupe Davis / BJK Cup": "D"}
PREFIJO_SACKMANN = "sackmann:atp:"

PATRON_SET = re.compile(r"(\d+)-(\d+)")


def cargar_partidos() -> pd.DataFrame:
    archivos = sorted(glob.glob(str(DATOS / "raw" / "atp_matches_[12]*.csv")))
    archivos += sorted(glob.glob(str(DATOS / "raw" / "atp_matches_qual_chall_*.csv")))
    df = pd.concat((pd.read_csv(a, low_memory=False) for a in archivos), ignore_index=True)
    df["fuente"] = "sackmann"
    return df


def verificar_recientes(d: pd.DataFrame) -> None:
    """Comprueba que los resultados recientes son razonables antes de usarlos.

    Se bajan siempre de la última versión de un repositorio de terceros que cambia a diario, sin
    versión fija: si llegara algo corrupto o manipulado, mejor abortar que reentrenar con ello.
    """
    problemas = []
    faltan = {"record_id", "match_date", "tourney_id", "tourney_name", "surface", "category", "score",
              "winner_id", "loser_id", "winner_name", "loser_name"} - set(d.columns)
    if faltan:
        raise SystemExit(f"Resultados recientes: faltan las columnas {sorted(faltan)}")

    dia = pd.to_datetime(d["match_date"], errors="coerce")
    hoy = pd.Timestamp.today().normalize()
    if dia.isna().any() or (dia < pd.Timestamp("2026-01-01")).any() or (dia > hoy + pd.Timedelta(days=1)).any():
        problemas.append("fechas vacías, anteriores a 2026 o futuras")
    if not d["surface"].isin(["Hard", "Clay", "Grass", "Carpet"]).all():
        problemas.append(f"superficies desconocidas: {sorted(set(d['surface'].dropna()) - {'Hard', 'Clay', 'Grass', 'Carpet'})}")
    if not d["score"].fillna("").str.contains(r"\d+-\d+").all():
        problemas.append("marcadores sin ningún set")
    if d["record_id"].duplicated().any():
        problemas.append("partidos repetidos")
    if d[["winner_name", "loser_name"]].isna().any().any() or (d["winner_id"] == d["loser_id"]).any():
        problemas.append("jugadores vacíos o un jugador contra sí mismo")
    cuotas = d.filter(like="_decimal")
    if (cuotas <= 1).any().any():
        problemas.append("cuotas menores o iguales que 1")
    # El día con más partidos de 2026 tiene 65; 300 solo se supera si alguien inventa resultados
    if len(d) and d.groupby("match_date").size().max() > 300:
        problemas.append("un día con más de 300 partidos")
    if problemas:
        raise SystemExit("Resultados recientes sospechosos, no se usan: " + "; ".join(problemas))


def cargar_recientes(archivo: pd.DataFrame) -> pd.DataFrame:
    """Resultados de Valuebetennis posteriores al archivo de Sackmann, en su mismo formato.

    No traen ronda, minutos, ranking ni estadísticas de saque. El orden dentro del torneo sale
    del día exacto de cada partido y la fecha del torneo es la de su primer partido, como en Sackmann.
    """
    rutas = sorted(glob.glob(str(DATOS / "raw" / "recientes" / "atp_*.csv.gz")))
    if not rutas:
        return pd.DataFrame()
    d = pd.concat((pd.read_csv(r, low_memory=False) for r in rutas), ignore_index=True)
    d = d[d["record_id"].str.startswith("vb:") & d["category"].isin(NIVEL_RECIENTES)]
    verificar_recientes(d)

    # Identidad: la verificada por el repositorio de origen o, si no la hay, un único jugador
    # del archivo con el mismo nombre y apellido que haya jugado en 2025-2026
    reconocidos = jugadores_recientes(archivo)
    recuperados = 0
    for lado in ("winner", "loser"):
        sin_verificar = ~d[f"{lado}_id"].str.startswith(PREFIJO_SACKMANN)
        nuevo = d.loc[sin_verificar, f"{lado}_name"].map(lambda n: reconocidos.get(clave_nombre(n)))
        d.loc[sin_verificar & nuevo.reindex(d.index).notna(), f"{lado}_id"] = PREFIJO_SACKMANN + nuevo.dropna().astype(int).astype(str)
        recuperados += nuevo.notna().sum()
    validos = d["winner_id"].str.startswith(PREFIJO_SACKMANN) & d["loser_id"].str.startswith(PREFIJO_SACKMANN)
    descartados = (~validos).sum()
    d = d[validos].copy()

    dia = pd.to_datetime(d["match_date"])
    inicio = dia.groupby(d["tourney_id"]).transform("min")
    sets_ganador = d["score"].map(lambda s: sum(int(a) > int(b) for a, b in PATRON_SET.findall(s)))
    nivel = d["category"].map(NIVEL_RECIENTES)
    r = pd.DataFrame({
        "tourney_id": d["tourney_id"], "tourney_name": d["tourney_name"], "surface": d["surface"],
        "tourney_level": nivel, "tourney_date": inicio.dt.strftime("%Y%m%d").astype(int),
        "match_num": (dia - inicio).dt.days,
        "winner_id": d["winner_id"].str.removeprefix(PREFIJO_SACKMANN).astype(int),
        "loser_id": d["loser_id"].str.removeprefix(PREFIJO_SACKMANN).astype(int),
        "winner_name": d["winner_name"], "loser_name": d["loser_name"], "score": d["score"],
        # En un Grand Slam, ganar con 3 sets significa cuadro final al mejor de 5
        "best_of": np.where((nivel == "G") & (sets_ganador == 3), 5, 3),
        "round": "",
        "fuente": "valuebetennis",
        # Cuotas de Pinnacle, para medir el modelo frente al mercado en estos partidos (08_seguimiento.py)
        "cuota_ganador_apertura": d["odds_open_winner_decimal"], "cuota_perdedor_apertura": d["odds_open_loser_decimal"],
        "cuota_ganador_cierre": d["odds_close_winner_decimal"], "cuota_perdedor_cierre": d["odds_close_loser_decimal"],
    })
    # Lo que ya está en el archivo de Sackmann no se repite
    r = r[r["tourney_date"] > archivo["tourney_date"].max()]
    r = con_ranking(r)
    print(f"Resultados recientes: {len(r):,} partidos ({recuperados:,} jugadores identificados por nombre, "
          f"{descartados:,} partidos descartados por jugadores sin identificar)")
    return r


def con_ranking(r: pd.DataFrame) -> pd.DataFrame:
    """Ranking ATP de cada jugador: el último publicado antes del torneo, como en Sackmann.

    Los resultados recientes no traen ranking y no hay ninguno publicado después del 8 de junio de
    2026, así que es el mismo ranking congelado que usa la app para pronosticar.
    """
    ranking = pd.read_csv(DATOS / "raw" / "atp_rankings_current.csv").rename(columns={"player": "id"})
    ranking = ranking.sort_values("rank").drop_duplicates(["ranking_date", "id"]).sort_values("ranking_date")
    r = r.sort_values("tourney_date")
    for lado in ("winner", "loser"):
        r = pd.merge_asof(r, ranking.rename(columns={"id": f"{lado}_id", "rank": f"{lado}_rank", "points": f"{lado}_rank_points"}),
                          left_on="tourney_date", right_on="ranking_date", by=f"{lado}_id",
                          allow_exact_matches=False).drop(columns="ranking_date")
    return r


def clave_nombre(nombre: str) -> tuple[str, str] | None:
    """Nombre y último apellido, sin tildes ni mayúsculas: "Taylor Harry Fritz" → ("taylor", "fritz")."""
    limpio = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode().lower()
    partes = re.sub(r"[^a-z ]", " ", limpio).split()
    return (partes[0], partes[-1]) if len(partes) >= 2 else None


def jugadores_recientes(archivo: pd.DataFrame) -> dict[tuple[str, str], int]:
    """Jugadores del archivo con partidos en 2025-2026, por nombre. Los nombres repetidos se descartan."""
    recientes = archivo[archivo["tourney_date"] >= 20250101]
    nombres = pd.concat([
        recientes[["winner_id", "winner_name"]].set_axis(["id", "nombre"], axis=1),
        recientes[["loser_id", "loser_name"]].set_axis(["id", "nombre"], axis=1),
    ]).drop_duplicates("id")
    nombres["clave"] = nombres["nombre"].map(clave_nombre)
    unicos = nombres.dropna(subset=["clave"]).drop_duplicates("clave", keep=False)
    return dict(zip(unicos["clave"], unicos["id"].astype(int)))


def juegos_totales(marcador: str) -> int:
    return sum(int(a) + int(b) for a, b in PATRON_SET.findall(marcador))


def estimar_minutos(df: pd.DataFrame) -> pd.Series:
    """Duración que falta (resultados recientes) a partir de los juegos jugados.

    El cansancio del modelo se mide en minutos: sin esto, un jugador con cuatro partidos en la
    última semana parecería no haber jugado ni un minuto.
    """
    juegos = df["score"].map(juegos_totales)
    con_duracion = df["minutes"].notna() & (df["minutes"] > 0) & (juegos > 0) & df["final_normal"]
    minutos_por_juego = df.loc[con_duracion, "minutes"].sum() / juegos[con_duracion].sum()
    # Solo en los recientes: los huecos de Sackmann se quedan como los vio el modelo al entrenar
    estimar = df["minutes"].isna() & (df["fuente"] == "valuebetennis")
    return df["minutes"].mask(estimar, (juegos * minutos_por_juego).round())


def limpiar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["winner_id", "loser_id", "score"])
    df = df.drop_duplicates(subset=["tourney_id", "winner_id", "loser_id", "round"])
    # Errores del origen: unos pocos partidos de un jugador contra sí mismo.
    df = df[df["winner_id"] != df["loser_id"]].copy()

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
    df["minutes"] = estimar_minutos(df)

    df = df.sort_values(["fecha", "tourney_id", "ronda_orden", "match_num"], kind="stable")
    df = df.reset_index(drop=True)
    df["orden"] = df.index
    return df


def main() -> None:
    archivo = cargar_partidos()
    df = limpiar(pd.concat([archivo, cargar_recientes(archivo)], ignore_index=True))
    df.to_parquet(DATOS / "partidos.parquet", index=False)
    print(f"{len(df):,} partidos | {df['fecha'].min():%Y-%m-%d} → {df['fecha'].max():%Y-%m-%d}")
    print(f"Circuito principal: {df['circuito_principal'].sum():,} | Challengers y previas: {(~df['circuito_principal']).sum():,}")
    print(f"Con estadísticas: {df['w_ace'].notna().sum():,} | Abandonos: {(~df['final_normal']).sum():,}")


if __name__ == "__main__":
    main()
