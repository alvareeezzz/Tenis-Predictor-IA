"""Fase 5 - Exporta lo que necesita el backend .NET: base de datos SQLite y modelo ONNX.

Para predecir un partido futuro hace falta el "estado actual" de cada jugador calculado igual
que en el entrenamiento. Se reutiliza el código de 04_features.py añadiendo a cada jugador un
partido virtual en la fecha de los datos, uno por superficie: las ventanas (52 semanas,
superficie, forma, cansancio) salen así con las mismas funciones que vio el modelo.

Salidas en backend/Datos/:
  tenis.db             jugadores, estado de cada jugador por superficie, estadísticas, partidos
  modelo.onnx          el modelo XGBoost en formato ONNX (lo ejecuta Microsoft.ML.OnnxRuntime)
  modelo.json          contrato con .NET: orden de las variables y cómo se construye cada una
  casos_prueba.json    partidos de ejemplo con su probabilidad esperada, para los tests de .NET
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd
import xgboost as xgb
from onnxmltools import convert_xgboost
from onnxmltools.convert.common.data_types import FloatTensorType

RAIZ = Path(__file__).parent
DATOS = RAIZ / "data"
SALIDA = RAIZ.parent / "backend" / "Datos"
SUPERFICIES = ["Hard", "Clay", "Grass"]
DIAS_ACTIVO = 730  # solo se exporta el estado de quien ha jugado en los 2 últimos años

# El cansancio y la inactividad solo tienen sentido si los datos están al día: con datos viejos,
# un jugador que no jugó el último torneo parecería lesionado meses después. Si los datos tienen
# más de DIAS_DATOS_FRESCOS, se ponen iguales para todos (una semana normal de descanso) y así no
# inclinan la predicción. Con la actualización semanal (actualizar.py) se usan los valores reales.
DIAS_DATOS_FRESCOS = 14
ACTIVIDAD_NEUTRA = {"partidos_14d": 0, "minutos_14d": 0, "minutos_torneo": 0, "dias_desde_ultimo_torneo": 7}


def importar(nombre: str):
    spec = importlib.util.spec_from_file_location(nombre.replace(".py", ""), RAIZ / nombre)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


features = importar("04_features.py")
entrenar = importar("05_entrenar.py")


# --- Estado actual de cada jugador --------------------------------------------------------------

def estado_jugadores(partidos, elo_partidos, elo_actual, jugadores, ranking) -> tuple[pd.DataFrame, pd.Timestamp]:
    largo = features.tabla_por_jugador(partidos, elo_partidos)
    hoy = int(largo["dia"].max()) + 1
    fecha_datos = pd.Timestamp("1960-01-01") + pd.Timedelta(days=hoy)
    activos = largo.loc[largo["dia"] > hoy - DIAS_ACTIVO, "jugador"].unique()

    bloques = []
    for i, superficie in enumerate(SUPERFICIES):
        virtual = pd.DataFrame({"jugador": activos, "rival": -1, "superficie_elo": superficie,
                                "tourney_id": "__estado__", "dia": hoy, "fecha": fecha_datos})
        virtual["orden"] = largo["orden"].max() + 1 + np.arange(len(activos))
        for c in [*features.SUMABLES, "minutes"]:
            virtual[c] = 0.0
        todo = pd.concat([largo, virtual], ignore_index=True)
        f = features.features_por_jugador(todo).iloc[len(largo):]
        f = f.drop(columns=["orden", "rival"]).rename(columns={"jugador": "player_id"})
        f["superficie"] = superficie
        bloques.append(f)
    estado = pd.concat(bloques, ignore_index=True)
    estado = estado.drop(columns=[c for c in estado if c.startswith("h2h")])  # el H2H depende del rival
    antiguedad = (pd.Timestamp.today().normalize() - fecha_datos).days
    if antiguedad > DIAS_DATOS_FRESCOS:
        print(f"  Datos de hace {antiguedad} días: cansancio e inactividad neutros para todos")
        for col, valor in ACTIVIDAD_NEUTRA.items():
            estado[col] = valor
    else:
        print(f"  Datos de hace {antiguedad} días: se usan el cansancio y la inactividad reales")
        # Desde el último torneo hasta hoy, no hasta el inicio del último torneo
        estado["dias_desde_ultimo_torneo"] = (estado["dias_desde_ultimo_torneo"] + antiguedad).clip(upper=365)

    # Elo, ranking y datos personales: las mismas transformaciones que en preparar() de 05_entrenar.py
    estado = estado.merge(elo_actual, on="player_id", how="left")
    estado["elo_sup"] = estado.apply(lambda r: r[f"elo_{r['superficie'].lower()}"], axis=1)
    estado["partidos_elo_sup"] = estado.apply(lambda r: r[f"partidos_{r['superficie'].lower()}"], axis=1)
    estado["elo_comb"] = (1 - entrenar.PESO_SUPERFICIE) * estado["elo"] + entrenar.PESO_SUPERFICIE * estado["elo_sup"]
    estado["log_experiencia"] = np.log1p(estado["partidos"])
    estado["log_experiencia_sup"] = np.log1p(estado["partidos_elo_sup"])

    estado = estado.merge(ranking, on="player_id", how="left")
    estado["log_rank"] = -np.log(estado["rank"].fillna(2000))
    estado["log_puntos_rank"] = np.log1p(estado["puntos_rank"].fillna(0))

    estado = estado.merge(jugadores[["player_id", "fecha_nacimiento", "altura", "mano"]], on="player_id", how="left")
    estado["edad"] = (fecha_datos - estado["fecha_nacimiento"]).dt.days / 365.25
    estado["zurdo"] = (estado["mano"] == "L").astype(int)
    return estado, fecha_datos


def comprobar_estado(estado: pd.DataFrame) -> None:
    """El partido virtual debe dar lo mismo que el cálculo directo de 04_features.py."""
    actuales = pd.read_parquet(DATOS / "estadisticas_actuales.parquet")
    a52 = actuales[actuales["ventana"] == "52s"].set_index("player_id")
    e = estado[estado["superficie"] == "Hard"].set_index("player_id")
    comunes = e.index.intersection(a52.index)
    for col in ("pct_victorias", "pct_puntos_totales", "aces_partido"):
        x, y = e.loc[comunes, f"{col}_52s"], a52.loc[comunes, col]
        iguales = np.isclose(x, y, equal_nan=True).mean()
        print(f"  {col}_52s igual al cálculo directo en el {iguales:.1%} de los jugadores")
        assert iguales > 0.999, col


# --- Modelo ONNX ----------------------------------------------------------------------------------

def exportar_onnx(columnas: list[str]) -> xgb.XGBClassifier:
    modelo = xgb.XGBClassifier()
    modelo.load_model(DATOS / "modelo_xgb.json")
    booster = modelo.get_booster()
    booster.feature_names = None  # el conversor espera las variables como f0, f1, ...
    onnx = convert_xgboost(booster, initial_types=[("variables", FloatTensorType([None, len(columnas)]))],
                           target_opset=15)
    (SALIDA / "modelo.onnx").write_bytes(onnx.SerializeToString())
    booster.feature_names = columnas
    return modelo


def comprobar_onnx(modelo: xgb.XGBClassifier, columnas: list[str]) -> None:
    """ONNX y XGBoost deben dar la misma probabilidad, también con valores desconocidos (NaN)."""
    f = entrenar.preparar(pd.read_parquet(DATOS / "features.parquet"))
    muestra = f[f["fecha"] >= "2023-01-01"].sample(2000, random_state=1)
    bases = json.loads((DATOS / "modelo_columnas.json").read_text())["bases"]
    x = entrenar.matriz(muestra, bases)[columnas]
    esperado = modelo.predict_proba(x)[:, 1]
    sesion = ort.InferenceSession(str(SALIDA / "modelo.onnx"))
    salidas = sesion.run(None, {"variables": x.to_numpy(np.float32)})
    obtenido = np.asarray(salidas[1])[:, 1]
    diferencia = np.abs(obtenido - esperado).max()
    print(f"  ONNX frente a XGBoost en {len(x):,} partidos ({x.isna().any(axis=1).mean():.0%} con NaN): "
          f"diferencia máxima {diferencia:.2e}")
    assert diferencia < 1e-4


# --- Predicción de referencia (lo que tendrá que hacer .NET) -------------------------------------

def h2h(partidos_db: pd.DataFrame, a: int, b: int, superficie: str, fecha_datos: pd.Timestamp) -> dict:
    entre = partidos_db[((partidos_db["ganador_id"] == a) & (partidos_db["perdedor_id"] == b))
                        | ((partidos_db["ganador_id"] == b) & (partidos_db["perdedor_id"] == a))]
    gana_a = entre["ganador_id"] == a
    sets_a = np.where(gana_a, entre["sets_ganador"], entre["sets_perdedor"]).sum()
    sets_b = np.where(gana_a, entre["sets_perdedor"], entre["sets_ganador"]).sum()
    sup = entre["superficie_elo"] == superficie
    reciente = pd.to_datetime(entre["fecha"]) > fecha_datos - pd.Timedelta(days=features.DIAS_H2H_RECIENTE)
    v = {"h2h_partidos": len(entre), "h2h_victorias": int(gana_a.sum()),
         "h2h_sup_partidos": int(sup.sum()), "h2h_sup_victorias": int((gana_a & sup).sum()),
         "h2h_3a_partidos": int(reciente.sum()), "h2h_3a_victorias": int((gana_a & reciente).sum()),
         "h2h_pct_sets": sets_a / (sets_a + sets_b) if sets_a + sets_b else np.nan}
    return v


def vector_partido(estado, partidos_db, fecha_datos, a, b, superficie, nivel="A", ronda="R32", mejor_de=3,
                   previa_a=0, previa_b=0) -> pd.DataFrame:
    """Construye la fila de un partido A contra B con el mismo formato que features.parquet."""
    fila = {"surface": superficie, "tourney_level": nivel, "round": ronda, "best_of": mejor_de,
            "circuito_principal": nivel != "C"}
    for lado, jugador, previa in (("a", a, previa_a), ("b", b, previa_b)):
        e = estado[(estado["player_id"] == jugador) & (estado["superficie"] == superficie)].iloc[0]
        for col, valor in e.items():
            fila[f"{col}_{lado}"] = valor
        fila[f"viene_de_previa_{lado}"] = previa
        rival = b if lado == "a" else a
        for col, valor in h2h(partidos_db, jugador, rival, superficie, fecha_datos).items():
            fila[f"{col}_{lado}"] = valor
    return pd.DataFrame([fila])


def predecir(sesion, columnas, bases, fila) -> float:
    p = [sesion.run(None, {"variables": entrenar.matriz(fila, bases, invertir=inv)[columnas].to_numpy(np.float32)})[1][0][1]
         for inv in (False, True)]
    return float((p[0] + 1 - p[1]) / 2)


# --- Main ---------------------------------------------------------------------------------------

def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    partidos = pd.read_parquet(DATOS / "partidos.parquet")
    elo_partidos = pd.read_parquet(DATOS / "partidos_elo.parquet")
    elo_actual = pd.read_parquet(DATOS / "elo_actual.parquet")

    jugadores = pd.read_csv(DATOS / "raw" / "atp_players.csv", low_memory=False)
    jugadores["nombre"] = (jugadores["name_first"].fillna("") + " " + jugadores["name_last"].fillna("")).str.strip()
    jugadores["fecha_nacimiento"] = pd.to_datetime(jugadores["dob"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    jugadores = jugadores.rename(columns={"height": "altura", "hand": "mano", "ioc": "pais"})

    ranking = pd.read_csv(DATOS / "raw" / "atp_rankings_current.csv")
    fecha_ranking = ranking["ranking_date"].max()
    ranking = ranking[ranking["ranking_date"] == fecha_ranking].rename(columns={"player": "player_id", "points": "puntos_rank"})[["player_id", "rank", "puntos_rank"]]
    # El CSV trae algún jugador repetido en la misma semana: se queda la mejor posición
    ranking = ranking.sort_values("rank").drop_duplicates("player_id")

    print("Calculando el estado actual de cada jugador...")
    estado, fecha_datos = estado_jugadores(partidos, elo_partidos, elo_actual, jugadores, ranking)
    comprobar_estado(estado)

    contrato = json.loads((DATOS / "modelo_columnas.json").read_text())
    bases, columnas = contrato["bases"], contrato["columnas"]
    bases_estado = [c for c in bases if not c.startswith("h2h") and c != "viene_de_previa"]

    # Partidos (para el H2H y la evolución del Elo)
    marcador = pd.DataFrame(partidos["score"].map(features.analizar_marcador).tolist(),
                            columns=["sets_g", "sets_p", "j_g", "j_p", "tb_g", "tb_p"])
    partidos_db = pd.DataFrame({
        "orden": partidos["orden"], "fecha": partidos["fecha"].dt.strftime("%Y-%m-%d"),
        "torneo": partidos["tourney_name"], "nivel": partidos["tourney_level"],
        "superficie": partidos["surface"], "superficie_elo": partidos["superficie_elo"],
        "ronda": partidos["round"], "mejor_de": partidos["best_of"],
        "ganador_id": partidos["winner_id"].astype(int), "perdedor_id": partidos["loser_id"].astype(int),
        "marcador": partidos["score"], "sets_ganador": marcador["sets_g"], "sets_perdedor": marcador["sets_p"],
        "minutos": partidos["minutes"], "circuito_principal": partidos["circuito_principal"].astype(int),
    }).merge(elo_partidos[["orden", "elo_w", "elo_l"]].rename(columns={"elo_w": "elo_ganador", "elo_l": "elo_perdedor"}), on="orden")

    ids = pd.unique(partidos_db[["ganador_id", "perdedor_id"]].to_numpy().ravel())
    jugadores_db = (jugadores[jugadores["player_id"].isin(ids)]
                    [["player_id", "nombre", "pais", "mano", "altura", "fecha_nacimiento"]]
                    .merge(ranking, on="player_id", how="left")
                    .merge(elo_actual.rename(columns={"partidos": "partidos_elo"}), on="player_id", how="left"))
    jugadores_db["fecha_nacimiento"] = jugadores_db["fecha_nacimiento"].dt.strftime("%Y-%m-%d")
    jugadores_db["ultimo_partido"] = jugadores_db["ultimo_partido"].dt.strftime("%Y-%m-%d")
    jugadores_db["activo"] = jugadores_db["player_id"].isin(estado["player_id"]).astype(int)

    estadisticas = pd.read_parquet(DATOS / "estadisticas_actuales.parquet")
    estadisticas = estadisticas[estadisticas["player_id"].isin(estado["player_id"])]

    informe = (DATOS / "informe_modelo.txt").read_text(encoding="utf-8")
    metadatos = {
        "fecha_datos": f"{fecha_datos:%Y-%m-%d}",
        "fecha_ranking": f"{pd.to_datetime(str(fecha_ranking)):%Y-%m-%d}",
        "peso_superficie": str(entrenar.PESO_SUPERFICIE),
        "fuente": "Jeff Sackmann (tennis_atp), CC BY-NC-SA 4.0; resultados recientes de Valuebetennis, CC BY 4.0",
        "informe_modelo": informe,
    }

    ruta_db = SALIDA / "tenis.db"
    try:
        ruta_db.unlink(missing_ok=True)
    except PermissionError:
        raise SystemExit("tenis.db está abierta: para la API (dotnet run) antes de exportar y arráncala después.")
    with sqlite3.connect(ruta_db) as con:
        jugadores_db.to_sql("jugadores", con, index=False)
        estado[["player_id", "superficie", *bases_estado]].to_sql("estado_jugador", con, index=False)
        estadisticas.to_sql("estadisticas", con, index=False)
        partidos_db.to_sql("partidos", con, index=False)
        pd.DataFrame(metadatos.items(), columns=["clave", "valor"]).to_sql("metadatos", con, index=False)
        con.executescript("""
            CREATE UNIQUE INDEX ix_jugadores ON jugadores(player_id);
            CREATE INDEX ix_jugadores_nombre ON jugadores(nombre);
            CREATE UNIQUE INDEX ix_estado ON estado_jugador(player_id, superficie);
            CREATE INDEX ix_estadisticas ON estadisticas(player_id);
            CREATE INDEX ix_partidos_ganador ON partidos(ganador_id, perdedor_id);
            CREATE INDEX ix_partidos_perdedor ON partidos(perdedor_id, ganador_id);
            VACUUM;
        """)
    print(f"tenis.db: {len(jugadores_db):,} jugadores ({len(estado) // 3:,} activos), {len(partidos_db):,} partidos, "
          f"{ruta_db.stat().st_size / 1e6:.0f} MB")

    print("Exportando el modelo a ONNX...")
    modelo = exportar_onnx(columnas)
    comprobar_onnx(modelo, columnas)

    (SALIDA / "modelo.json").write_text(json.dumps({
        "columnas": columnas,
        "variables_jugador": bases,
        "variables_estado": bases_estado,
        "h2h_comunes": entrenar.H2H_COMUNES,
        "orden_rondas": entrenar.ORDEN_RONDAS,
        "dias_h2h_reciente": features.DIAS_H2H_RECIENTE,
        "peso_superficie": entrenar.PESO_SUPERFICIE,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Casos de prueba para .NET: probabilidad esperada de varios partidos entre jugadores actuales
    sesion = ort.InferenceSession(str(SALIDA / "modelo.onnx"))
    nombre = jugadores_db.set_index("player_id")["nombre"]
    top = jugadores_db[jugadores_db["activo"] == 1].nsmallest(12, "rank")["player_id"].tolist()
    casos = []
    for i, (a, b) in enumerate(zip(top[::2], top[1::2])):
        for superficie, nivel, ronda, mejor_de in (("Hard", "M", "QF", 3), ("Clay", "G", "F", 5), ("Grass", "A", "R16", 3)):
            fila = vector_partido(estado, partidos_db, fecha_datos, a, b, superficie, nivel, ronda, mejor_de)
            casos.append({"jugador_a": int(a), "jugador_b": int(b), "nombre_a": nombre[a], "nombre_b": nombre[b],
                          "superficie": superficie, "nivel": nivel, "ronda": ronda, "mejor_de": mejor_de,
                          "prob_a": round(predecir(sesion, columnas, bases, fila), 6)})
    (SALIDA / "casos_prueba.json").write_text(json.dumps(casos, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEjemplos (datos a {fecha_datos:%Y-%m-%d}):")
    for c in casos:
        print(f"  {c['nombre_a']:<22} vs {c['nombre_b']:<22} {c['superficie']:<5} {c['nivel']} {c['ronda']:<3} "
              f"→ {c['prob_a']:.1%}")


if __name__ == "__main__":
    main()
