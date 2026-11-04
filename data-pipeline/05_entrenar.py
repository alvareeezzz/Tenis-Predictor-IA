"""Fase 4 - Entrena y evalúa los modelos de predicción.

Validación temporal (nunca se mezclan partidos al azar):
  entrenamiento  1991-2020  (circuito principal, Challengers y previas)
  validación     2021-2022  (parada temprana y elección del modelo)
  prueba         2023 en adelante, solo circuito principal: se usa una única vez, al final

Las variables son diferencias A - B. Al entrenar, cada partido se añade también con A y B
intercambiados y al predecir se promedian ambas versiones: así P(A gana) = 1 - P(B gana).

Salidas:
  data/modelo_xgb.json        modelo final (reentrenado con todos los partidos)
  data/modelo_columnas.json   orden de las variables que espera el modelo
  data/informe_modelo.txt     métricas de la evaluación
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATOS = Path(__file__).parent / "data"

FIN_ENTRENAMIENTO = "2021-01-01"
FIN_VALIDACION = "2023-01-01"
PESO_SUPERFICIE = 0.4  # el mismo que en 03_elo.py
ORDEN_RONDAS = {"Q1": -4, "Q2": -3, "Q3": -2, "Q4": -1, "ER": 0, "RR": 1, "R128": 2, "R64": 3,
                "R32": 4, "R16": 5, "QF": 6, "SF": 7, "BR": 8, "F": 9}

# Columnas de jugador que no son variables del modelo
NO_VARIABLES = {"id", "nombre", "rank", "puntos_rank", "partidos_elo", "partidos_elo_sup"}
# En el H2H el número de partidos es el mismo para los dos: va como contexto, no como diferencia
H2H_COMUNES = ["h2h_partidos", "h2h_sup_partidos", "h2h_3a_partidos"]

PARAMETROS_XGB = dict(
    max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.7,
    min_child_weight=20, reg_lambda=5.0, n_estimators=3000, eval_metric="logloss",
    early_stopping_rounds=100, tree_method="hist", random_state=42,
)


# --- Variables ---------------------------------------------------------------------------------

def preparar(f: pd.DataFrame) -> pd.DataFrame:
    """Añade al lado de A y B las transformaciones que hacen falta antes de restar."""
    f = f.copy()
    for s in "ab":
        f[f"elo_comb_{s}"] = (1 - PESO_SUPERFICIE) * f[f"elo_{s}"] + PESO_SUPERFICIE * f[f"elo_sup_{s}"]
        # El ranking va en escala logarítmica: no es lo mismo pasar del 1 al 10 que del 200 al 210
        f[f"log_rank_{s}"] = -np.log(f[f"rank_{s}"].fillna(2000))
        f[f"log_puntos_rank_{s}"] = np.log1p(f[f"puntos_rank_{s}"].fillna(0))
        f[f"log_experiencia_{s}"] = np.log1p(f[f"partidos_elo_{s}"])
        f[f"log_experiencia_sup_{s}"] = np.log1p(f[f"partidos_elo_sup_{s}"])
    return f


def variables_jugador(f: pd.DataFrame) -> list[str]:
    bases = [c[:-2] for c in f.columns if c.endswith("_a") and f"{c[:-2]}_b" in f.columns]
    return [b for b in bases if b not in NO_VARIABLES and b not in H2H_COMUNES]


def matriz(f: pd.DataFrame, bases: list[str], invertir: bool = False) -> pd.DataFrame:
    """Variables del modelo. Con `invertir`, el mismo partido visto con A y B intercambiados."""
    a, b = ("b", "a") if invertir else ("a", "b")
    x = pd.DataFrame({f"dif_{c}": f[f"{c}_{a}"].astype(float) - f[f"{c}_{b}"].astype(float) for c in bases})
    # Contexto del partido (igual sea cual sea el orden de los jugadores)
    for c in H2H_COMUNES:
        x[c] = f[f"{c}_a"]
    x["elo_medio_partido"] = (f["elo_comb_a"] + f["elo_comb_b"]) / 2
    x["mejor_de_5"] = (f["best_of"] == 5).astype(int)
    x["ronda"] = f["round"].map(ORDEN_RONDAS).fillna(0)
    x["circuito_principal"] = f["circuito_principal"].astype(int)
    for sup in ("Hard", "Clay", "Grass", "Carpet"):
        x[f"superficie_{sup.lower()}"] = (f["surface"] == sup).astype(int)
    for nivel, nombre in (("G", "grand_slam"), ("M", "masters"), ("F", "finals"), ("D", "copa_davis"), ("C", "challenger")):
        x[f"nivel_{nombre}"] = (f["tourney_level"] == nivel).astype(int)
    return x


def con_espejo(f: pd.DataFrame, bases: list[str]) -> tuple[pd.DataFrame, np.ndarray]:
    x = pd.concat([matriz(f, bases), matriz(f, bases, invertir=True)], ignore_index=True)
    y = np.concatenate([f["gana_a"].to_numpy(), 1 - f["gana_a"].to_numpy()])
    return x, y


def predecir(modelo, f: pd.DataFrame, bases: list[str]) -> np.ndarray:
    p_a = modelo.predict_proba(matriz(f, bases))[:, 1]
    p_b = modelo.predict_proba(matriz(f, bases, invertir=True))[:, 1]
    return (p_a + 1 - p_b) / 2


# --- Modelos -----------------------------------------------------------------------------------

class RegresionLogistica:
    """Regresión logística sobre las diferencias; un valor desconocido se trata como 'sin diferencia'."""

    def __init__(self):
        self.pipeline = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000))

    def fit(self, x, y):
        self.pipeline.fit(x.fillna(0), y)
        return self

    def predict_proba(self, x):
        return self.pipeline.predict_proba(x.fillna(0))


def metricas(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"acierto": ((p > 0.5) == y).mean(), "log_loss": log_loss(y, p), "brier": brier_score_loss(y, p)}


def tabla_calibracion(y: np.ndarray, p: np.ndarray) -> str:
    """Probabilidad del favorito frente a cuántas veces ganó realmente."""
    fav = np.maximum(p, 1 - p)
    gana_fav = np.where(p >= 0.5, y, 1 - y)
    tramos = pd.cut(fav, [0.5, 0.6, 0.7, 0.8, 0.9, 1.0], include_lowest=True)
    t = pd.DataFrame({"tramo": tramos, "prob": fav, "gana": gana_fav}).groupby("tramo", observed=True)
    filas = [f"  {'Prob. del favorito':<20} {'Partidos':>8} {'Predicho':>9} {'Real':>7}"]
    for tramo, g in t:
        filas.append(f"  {str(tramo):<20} {len(g):>8,} {g['prob'].mean():>9.1%} {g['gana'].mean():>7.1%}")
    return "\n".join(filas)


def main() -> None:
    f = preparar(pd.read_parquet(DATOS / "features.parquet"))
    f = f[f["final_normal"]].reset_index(drop=True)
    bases = variables_jugador(f)

    entrenamiento = f[f["fecha"] < FIN_ENTRENAMIENTO]
    validacion = f[(f["fecha"] >= FIN_ENTRENAMIENTO) & (f["fecha"] < FIN_VALIDACION) & f["circuito_principal"]]
    prueba = f[(f["fecha"] >= FIN_VALIDACION) & f["circuito_principal"]]
    print(f"Entrenamiento {len(entrenamiento):,} | validación {len(validacion):,} | prueba {len(prueba):,} partidos")

    x_ent, y_ent = con_espejo(entrenamiento, bases)
    x_val, y_val = con_espejo(validacion, bases)
    print(f"{x_ent.shape[1]} variables")

    logistica = RegresionLogistica().fit(x_ent, y_ent)
    arbol = xgb.XGBClassifier(**PARAMETROS_XGB)
    arbol.fit(x_ent, y_ent, eval_set=[(x_val, y_val)], verbose=False)
    print(f"XGBoost: {arbol.best_iteration + 1} árboles (parada temprana en validación)")

    elo = lambda d: 1 / (1 + 10 ** ((d["elo_comb_b"] - d["elo_comb_a"]) / 400))
    ranking = lambda d: np.where(d["rank_a"].fillna(9999) < d["rank_b"].fillna(9999), 1.0,
                                 np.where(d["rank_a"].fillna(9999) > d["rank_b"].fillna(9999), 0.0, 0.5))
    modelos = {
        "Ranking ATP": ranking,
        "Elo combinado": elo,
        "Regresión logística": lambda d: predecir(logistica, d, bases),
        "XGBoost": lambda d: predecir(arbol, d, bases),
    }

    informe = []
    for nombre_conjunto, conjunto in (("VALIDACIÓN 2021-2022", validacion), ("PRUEBA 2023-2026", prueba)):
        y = conjunto["gana_a"].to_numpy()
        informe.append(f"\n{nombre_conjunto} ({len(conjunto):,} partidos del circuito principal)")
        informe.append(f"  {'Modelo':<22} {'Acierto':>8} {'Log loss':>9} {'Brier':>7}")
        for nombre, modelo in modelos.items():
            p = np.asarray(modelo(conjunto), dtype=float)
            m = metricas(y, p)
            if nombre == "Ranking ATP":
                acierto = ((p > 0.5) == y).mean() + 0.5 * (p == 0.5).mean()
                informe.append(f"  {nombre:<22} {acierto:>8.1%} {'-':>9} {'-':>7}")
            else:
                informe.append(f"  {nombre:<22} {m['acierto']:>8.1%} {m['log_loss']:>9.4f} {m['brier']:>7.4f}")

    y = prueba["gana_a"].to_numpy()
    p = predecir(arbol, prueba, bases)
    informe.append("\nCalibración de XGBoost en prueba:")
    informe.append(tabla_calibracion(y, p))

    informe.append("\nAcierto de XGBoost en prueba por superficie y nivel:")
    acierta = (p > 0.5) == y
    for col, nombres in (("surface", None), ("tourney_level", {"G": "Grand Slam", "M": "Masters", "A": "ATP 250/500", "D": "Copa Davis", "F": "Finals", "O": "Juegos Olímp."})):
        for valor, g in pd.Series(acierta).groupby(prueba[col].to_numpy()):
            if len(g) >= 50:
                informe.append(f"  {(nombres or {}).get(valor, valor):<14} {g.mean():>6.1%}  ({len(g):,} partidos)")

    importancia = pd.Series(arbol.get_booster().get_score(importance_type="gain")).sort_values(ascending=False)
    informe.append("\nVariables más importantes de XGBoost (ganancia media):")
    for nombre, valor in importancia.head(15).items():
        informe.append(f"  {nombre:<36} {valor:>8.1f}")

    texto = "\n".join(informe)
    print(texto)
    (DATOS / "informe_modelo.txt").write_text(texto, encoding="utf-8")

    # Modelo final para la app: mismos parámetros y nº de árboles, entrenado con todos los partidos
    todos = f[f["fecha"] >= "1991-01-01"]
    x_todos, y_todos = con_espejo(todos, bases)
    parametros = {k: v for k, v in PARAMETROS_XGB.items() if k != "early_stopping_rounds"}
    parametros["n_estimators"] = arbol.best_iteration + 1
    final = xgb.XGBClassifier(**parametros).fit(x_todos, y_todos)
    final.save_model(DATOS / "modelo_xgb.json")
    (DATOS / "modelo_columnas.json").write_text(json.dumps({"bases": bases, "columnas": list(x_todos.columns)}, indent=2))
    print(f"\nModelo final entrenado con {len(todos):,} partidos y guardado en data/modelo_xgb.json")


if __name__ == "__main__":
    main()
