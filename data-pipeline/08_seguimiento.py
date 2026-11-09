"""Mejora 1 - Acierto real: el modelo frente a partidos jugados después de entrenarlo.

La prueba de 05_entrenar.py usa datos completos (estadísticas de saque, ranking al día). En la app,
los partidos recientes no traen estadísticas de saque y el ranking está congelado en junio de 2026:
esto mide cuánto acierta el modelo en esas condiciones.

    python data-pipeline/08_seguimiento.py                    # partidos posteriores al modelo guardado
    python data-pipeline/08_seguimiento.py --desde 2026-06-02 # simula haber entrenado hasta esa fecha

Con --desde se entrena un modelo temporal (mismos parámetros y nº de árboles que el guardado) solo con
los partidos anteriores a esa fecha. Cada ejecución añade una fila a data/seguimiento.csv.
"""

import argparse
import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

RAIZ = Path(__file__).parent
DATOS = RAIZ / "data"
MINIMO_PARTIDOS = 100


def importar(nombre: str):
    spec = importlib.util.spec_from_file_location(nombre.replace(".py", ""), RAIZ / nombre)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


entrenar = importar("05_entrenar.py")


def prob_mercado(cuota_a: pd.Series, cuota_b: pd.Series) -> pd.Series:
    validas = (cuota_a > 1) & (cuota_b > 1)
    return ((1 / cuota_a) / (1 / cuota_a + 1 / cuota_b)).where(validas)


def modelo_hasta(f: pd.DataFrame, bases: list[str], desde: pd.Timestamp, arboles: int) -> xgb.XGBClassifier:
    """Mismo modelo que el guardado, pero entrenado solo con lo anterior a `desde`."""
    historia = f[(f["fecha"] >= "1991-01-01") & (f["fecha"] < desde)]
    x, y = entrenar.con_espejo(historia, bases)
    parametros = {k: v for k, v in entrenar.PARAMETROS_XGB.items() if k != "early_stopping_rounds"}
    parametros["n_estimators"] = arboles
    print(f"Entrenando un modelo con {len(historia):,} partidos anteriores al {desde:%Y-%m-%d}...")
    return xgb.XGBClassifier(**parametros).fit(x, y, sample_weight=entrenar.pesos(historia, desde))


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argumentos.add_argument("--desde", help="fecha (AAAA-MM-DD): simula haber entrenado el modelo hasta ese día")
    desde_arg = argumentos.parse_args().desde

    contrato = json.loads((DATOS / "modelo_columnas.json").read_text())
    bases = contrato["bases"]
    guardado = xgb.XGBClassifier()
    guardado.load_model(DATOS / "modelo_xgb.json")
    calibracion = contrato.get("calibracion", 1.0)

    f = entrenar.preparar(pd.read_parquet(DATOS / "features.parquet"))
    f = f[f["final_normal"]].reset_index(drop=True)

    if desde_arg:
        desde = pd.Timestamp(desde_arg)
        modelo = modelo_hasta(f, bases, desde, guardado.get_booster().num_boosted_rounds())
        # La calibración también solo con lo que se sabía entonces: los dos años anteriores a `desde`
        previas = pd.read_parquet(DATOS / "predicciones_prueba.parquet")
        previas = previas[(previas["fecha"] < desde) & (previas["fecha"] >= desde - pd.Timedelta(days=730))]
        calibracion = entrenar.ajustar_calibracion(previas["gana_a"].to_numpy(), previas["prob_xgb_sin_calibrar"].to_numpy())
        print(f"Calibración con los {len(previas):,} partidos de los dos años anteriores: a = {calibracion:.3f}")
    else:
        if "entrenado_hasta" not in contrato:
            raise SystemExit("El modelo guardado no dice hasta cuándo se entrenó: reentrénalo (05_entrenar.py) o usa --desde.")
        desde = pd.Timestamp(contrato["entrenado_hasta"]) + pd.Timedelta(days=1)
        modelo = guardado

    nuevos = f[(f["fecha"] >= desde) & f["circuito_principal"]].copy()
    if len(nuevos) < MINIMO_PARTIDOS:
        raise SystemExit(f"Solo hay {len(nuevos)} partidos del circuito principal desde el {desde:%Y-%m-%d}: "
                         f"hacen falta al menos {MINIMO_PARTIDOS} para que el acierto signifique algo.")

    nuevos["prob_modelo"] = entrenar.calibrar(entrenar.predecir(modelo, nuevos, bases), calibracion)
    nuevos["prob_elo"] = 1 / (1 + 10 ** ((nuevos["elo_comb_b"] - nuevos["elo_comb_a"]) / 400))

    # Cuotas de Pinnacle, guardadas por 02_limpiar.py en las filas recientes
    cuotas = pd.read_parquet(DATOS / "partidos.parquet").set_index("orden")
    for momento in ("apertura", "cierre"):
        if f"cuota_ganador_{momento}" not in cuotas:
            continue
        ganador = nuevos["orden"].map(cuotas[f"cuota_ganador_{momento}"])
        perdedor = nuevos["orden"].map(cuotas[f"cuota_perdedor_{momento}"])
        a_gana = nuevos["gana_a"] == 1
        nuevos[f"prob_{momento}"] = prob_mercado(ganador.where(a_gana, perdedor), perdedor.where(a_gana, ganador))

    y = nuevos["gana_a"].to_numpy()
    informe = [f"Partidos del circuito principal desde el {desde:%Y-%m-%d}: {len(nuevos):,} "
               f"(hasta el {nuevos['fecha'].max():%Y-%m-%d}, fecha de inicio de torneo)"]
    informe.append(f"  {'Método':<24} {'Partidos':>8} {'Acierto':>8} {'Log loss':>9}")
    filas = [("Elo combinado", "prob_elo"), ("Modelo (la app)", "prob_modelo"),
             ("Pinnacle, apertura", "prob_apertura"), ("Pinnacle, cierre", "prob_cierre")]
    resumen = {}
    for nombre, col in filas:
        if col not in nuevos:
            continue
        hay = nuevos[col].notna().to_numpy()
        m = entrenar.metricas(y[hay], nuevos.loc[hay, col].to_numpy())
        resumen[col] = m
        informe.append(f"  {nombre:<24} {hay.sum():>8,} {m['acierto']:>8.1%} {m['log_loss']:>9.4f}")

    # Con cuota, en los mismos partidos: la comparación justa con el mercado
    if "prob_cierre" in nuevos:
        con_cuota = nuevos["prob_cierre"].notna().to_numpy()
        m = entrenar.metricas(y[con_cuota], nuevos.loc[con_cuota, "prob_modelo"].to_numpy())
        informe.append(f"  {'Modelo, mismos partidos':<24} {con_cuota.sum():>8,} {m['acierto']:>8.1%} {m['log_loss']:>9.4f}")

    informe.append("\nAcierto del modelo por tipo de torneo:")
    acierta = (nuevos["prob_modelo"] > 0.5) == (nuevos["gana_a"] == 1)
    nombres = {"G": "Grand Slam", "M": "Masters", "A": "ATP 250/500", "D": "Copa Davis", "F": "Finals"}
    for nivel, g in acierta.groupby(nuevos["tourney_level"]):
        informe.append(f"  {nombres.get(nivel, nivel):<14} {g.mean():>6.1%}  ({len(g):,} partidos)")

    informe.append("\nCalibración del modelo:")
    informe.append(entrenar.tabla_calibracion(y, nuevos["prob_modelo"].to_numpy()))

    texto = "\n".join(informe)
    print(texto)
    (DATOS / "informe_seguimiento.txt").write_text(texto, encoding="utf-8")

    fila = pd.DataFrame([{
        "fecha_ejecucion": date.today().isoformat(), "desde": f"{desde:%Y-%m-%d}", "simulado": bool(desde_arg),
        "partidos": len(nuevos), "acierto_modelo": round(resumen["prob_modelo"]["acierto"], 4),
        "log_loss_modelo": round(resumen["prob_modelo"]["log_loss"], 4),
        "acierto_elo": round(resumen["prob_elo"]["acierto"], 4),
        "acierto_pinnacle_cierre": round(resumen["prob_cierre"]["acierto"], 4) if "prob_cierre" in resumen else None,
    }])
    historial = DATOS / "seguimiento.csv"
    fila.to_csv(historial, mode="a", header=not historial.exists(), index=False)


if __name__ == "__main__":
    main()
