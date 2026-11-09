"""Fase 4 - Entrena y evalúa los modelos de predicción.

Validación temporal (nunca se mezclan partidos al azar):
  entrenamiento  1991-2020  (circuito principal, Challengers y previas)
  validación     2021-2022  (parada temprana y elección del modelo)
  prueba         2023 en adelante, solo circuito principal: se usa una única vez, al final

Las variables son diferencias A - B. Al entrenar, cada partido se añade también con A y B
intercambiados y al predecir se promedian ambas versiones: así P(A gana) = 1 - P(B gana).

Calibración: desde 2023 el circuito es más imprevisible y el modelo se pasa de confiado un par de
puntos. Se corrige con un solo parámetro (escalado por temperatura, p' = sigmoide(a · logit p)), que
mantiene P(A) + P(B) = 1. En 2021-2022 no hacía falta (a ≈ 1), así que se ajusta con los dos últimos
años de predicciones fuera de muestra; el informe comprueba que funciona en partidos que no usó.

Salidas:
  data/modelo_xgb.json        modelo final (reentrenado con todos los partidos)
  data/modelo_columnas.json   orden de las variables, calibración y fecha hasta la que se entrenó
  data/informe_modelo.txt     métricas de la evaluación
  data/predicciones_prueba.parquet  probabilidades en la prueba, para compararlas con las cuotas
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATOS = Path(__file__).parent / "data"

FIN_ENTRENAMIENTO = "2021-01-01"
FIN_VALIDACION = "2023-01-01"
PESO_SUPERFICIE = {"Hard": 0.4, "Clay": 0.45, "Grass": 0.35}  # el mismo que en 03_elo.py
ORDEN_RONDAS = {"Q1": -4, "Q2": -3, "Q3": -2, "Q4": -1, "ER": 0, "RR": 1, "R128": 2, "R64": 3,
                "R32": 4, "R16": 5, "QF": 6, "SF": 7, "BR": 8, "F": 9}

# Columnas de jugador que no son variables del modelo. El ranking ATP tampoco: en validación no
# aporta nada sobre el Elo (log loss 0,5830 sin él, 0,5831 con él) y en la app se queda viejo,
# porque ninguna fuente abierta publica los rankings posteriores a junio de 2026.
NO_VARIABLES = {"id", "nombre", "rank", "puntos_rank", "partidos_elo", "partidos_elo_sup", "log_rank", "log_puntos_rank"}
# En el H2H el número de partidos es el mismo para los dos: va como contexto, no como diferencia
H2H_COMUNES = ["h2h_partidos", "h2h_sup_partidos", "h2h_3a_partidos"]

# Elegidos con Optuna (30 pruebas, mejor log loss en validación)
PARAMETROS_XGB = dict(
    max_depth=4, learning_rate=0.05, subsample=0.61, colsample_bytree=0.70, min_child_weight=58,
    reg_lambda=10.1, reg_alpha=4.5, gamma=0.096, n_estimators=3000, eval_metric="logloss",
    early_stopping_rounds=100, tree_method="hist", random_state=42,
)
# Peso de cada partido al entrenar: el tenis de hace 14 años vale la mitad que el de hoy, y los
# Challengers y las previas, un 40 % de lo que vale un partido del circuito principal
VIDA_MEDIA_ANIOS = 13.6
PESO_NO_PRINCIPAL = 0.41
# La calibración se ajusta con las predicciones fuera de muestra de los dos últimos años
VENTANA_CALIBRACION = pd.Timedelta(days=730)
NOMBRES_NIVEL = {"G": "Grand Slam", "M": "Masters", "A": "ATP 250/500", "D": "Copa Davis", "F": "Finals", "O": "Juegos Olímp."}


# --- Variables ---------------------------------------------------------------------------------

def cargar_features() -> pd.DataFrame:
    """Los partidos con los que se entrena y se evalúa (04_features.py ya empieza en 1991)."""
    f = preparar(pd.read_parquet(DATOS / "features.parquet"))
    return f[f["final_normal"]].reset_index(drop=True)


def preparar(f: pd.DataFrame) -> pd.DataFrame:
    """Añade al lado de A y B las transformaciones que hacen falta antes de restar."""
    f = f.copy()
    peso = f["superficie_elo"].map(PESO_SUPERFICIE)
    for s in "ab":
        f[f"elo_comb_{s}"] = (1 - peso) * f[f"elo_{s}"] + peso * f[f"elo_sup_{s}"]
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


def pesos(f: pd.DataFrame, hasta: str | pd.Timestamp) -> np.ndarray:
    """Peso de cada fila de con_espejo(f): menos para lo antiguo y para lo que no es circuito principal."""
    anios = (pd.Timestamp(hasta) - f["fecha"]).dt.days.to_numpy() / 365.25
    w = np.exp2(-anios / VIDA_MEDIA_ANIOS) * np.where(f["circuito_principal"], 1.0, PESO_NO_PRINCIPAL)
    return np.tile(w, 2)


def prob_elo(d: pd.DataFrame) -> pd.Series:
    """Probabilidad de que gane A según el Elo combinado (la misma fórmula que prob_victoria de 03_elo.py)."""
    return 1 / (1 + 10 ** ((d["elo_comb_b"] - d["elo_comb_a"]) / 400))


def entrenar_final(historia: pd.DataFrame, bases: list[str], arboles: int, hasta: pd.Timestamp) -> xgb.XGBClassifier:
    """El modelo de la app: los parámetros elegidos, sin parada temprana y con un número fijo de árboles."""
    x, y = con_espejo(historia, bases)
    parametros = {k: v for k, v in PARAMETROS_XGB.items() if k != "early_stopping_rounds"}
    parametros["n_estimators"] = arboles
    return xgb.XGBClassifier(**parametros).fit(x, y, sample_weight=pesos(historia, hasta))


def predecir(modelo, f: pd.DataFrame, bases: list[str]) -> np.ndarray:
    p_a = modelo.predict_proba(matriz(f, bases))[:, 1]
    p_b = modelo.predict_proba(matriz(f, bases, invertir=True))[:, 1]
    return (p_a + 1 - p_b) / 2


def calibrar(p: np.ndarray, a: float) -> np.ndarray:
    """Escalado por temperatura: a < 1 acerca las probabilidades al 50 %, a > 1 las aleja."""
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return 1 / (1 + np.exp(-a * np.log(p / (1 - p))))


def ajustar_calibracion(y: np.ndarray, p: np.ndarray) -> float:
    return minimize_scalar(lambda a: log_loss(y, calibrar(p, a)), bounds=(0.5, 2.0), method="bounded").x


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
    f = cargar_features()
    bases = variables_jugador(f)

    entrenamiento = f[f["fecha"] < FIN_ENTRENAMIENTO]
    validacion = f[(f["fecha"] >= FIN_ENTRENAMIENTO) & (f["fecha"] < FIN_VALIDACION) & f["circuito_principal"]]
    prueba = f[(f["fecha"] >= FIN_VALIDACION) & f["circuito_principal"]]
    print(f"Entrenamiento {len(entrenamiento):,} | validación {len(validacion):,} | prueba {len(prueba):,} partidos")

    x_ent, y_ent = con_espejo(entrenamiento, bases)
    x_val, y_val = con_espejo(validacion, bases)
    print(f"{x_ent.shape[1]} variables")
    columnas = list(x_ent.columns)

    logistica = RegresionLogistica().fit(x_ent, y_ent)
    arbol = xgb.XGBClassifier(**PARAMETROS_XGB)
    arbol.fit(x_ent, y_ent, sample_weight=pesos(entrenamiento, FIN_ENTRENAMIENTO), eval_set=[(x_val, y_val)], verbose=False)
    print(f"XGBoost: {arbol.best_iteration + 1} árboles (parada temprana en validación)")
    del x_ent, x_val  # el modelo final construye su propia matriz: así no conviven las dos en memoria

    # El ranking no da probabilidades: 1 si A va por delante, 0 si va por detrás, 0,5 sin ranking de ninguno
    ranking = lambda d: 0.5 + 0.5 * np.sign(d["rank_b"].fillna(9999) - d["rank_a"].fillna(9999))
    modelos = {
        "Ranking ATP": ranking,
        "Elo combinado": prob_elo,
        "Regresión logística": lambda d: predecir(logistica, d, bases),
        "XGBoost": lambda d: predecir(arbol, d, bases),
    }

    informe = []
    for nombre_conjunto, conjunto in (("VALIDACIÓN 2021-2022", validacion), ("PRUEBA 2023-2026", prueba)):
        y = conjunto["gana_a"].to_numpy()
        informe.append(f"\n{nombre_conjunto} ({len(conjunto):,} partidos del circuito principal)")
        informe.append(f"  {'Modelo':<22} {'Acierto':>8} {'Log loss':>9} {'Brier':>7}")
        probs = {nombre: np.asarray(modelo(conjunto), dtype=float) for nombre, modelo in modelos.items()}
        for nombre, p in probs.items():
            if nombre == "Ranking ATP":
                acierto = np.where(p == 0.5, 0.5, (p > 0.5) == y).mean()  # sin ranking de ninguno: medio acierto
                informe.append(f"  {nombre:<22} {acierto:>8.1%} {'-':>9} {'-':>7}")
            else:
                m = metricas(y, p)
                informe.append(f"  {nombre:<22} {m['acierto']:>8.1%} {m['log_loss']:>9.4f} {m['brier']:>7.4f}")

    # Tras el bucle, `y` y `probs` son los de la prueba
    p_sin_calibrar = probs["XGBoost"]

    # Calibración. Comprobación honesta: ajustada con la primera mitad de la prueba, medida en la segunda
    mitad = (prueba["fecha"] < "2025-01-01").to_numpy()
    a_mitad = ajustar_calibracion(y[mitad], p_sin_calibrar[mitad])
    y_2a, p_2a = y[~mitad], p_sin_calibrar[~mitad]
    p_2a_calibrada = calibrar(p_2a, a_mitad)
    antes, despues = metricas(y_2a, p_2a), metricas(y_2a, p_2a_calibrada)
    informe.append(f"\nCalibración ajustada con 2023-2024 (a = {a_mitad:.3f}) y medida en {len(y_2a):,} partidos de 2025-2026:")
    informe.append(f"  log loss {antes['log_loss']:.4f} → {despues['log_loss']:.4f} | Brier {antes['brier']:.4f} → {despues['brier']:.4f}")
    informe.append("  sin calibrar:")
    informe.append(tabla_calibracion(y_2a, p_2a))
    informe.append("  calibrado:")
    informe.append(tabla_calibracion(y_2a, p_2a_calibrada))
    # La de la app: con los dos últimos años de la prueba
    recientes = (prueba["fecha"] >= prueba["fecha"].max() - VENTANA_CALIBRACION).to_numpy()
    calibracion = ajustar_calibracion(y[recientes], p_sin_calibrar[recientes])
    informe.append(f"Calibración de la app, ajustada con los {recientes.sum():,} partidos de los dos últimos años: a = {calibracion:.3f}")
    p = calibrar(p_sin_calibrar, calibracion)

    # Predicciones del modelo que no vio la prueba, para compararlas con las cuotas (07_comparar_cuotas.py)
    pd.DataFrame({"orden": prueba["orden"].to_numpy(), "fecha": prueba["fecha"].to_numpy(), "gana_a": y,
                  "id_a": prueba["id_a"].to_numpy(), "prob_xgb": p, "prob_xgb_sin_calibrar": p_sin_calibrar,
                  "prob_logistica": probs["Regresión logística"],
                  "prob_elo": probs["Elo combinado"]}).to_parquet(DATOS / "predicciones_prueba.parquet", index=False)

    informe.append("\nAcierto de XGBoost en prueba por superficie y nivel:")
    acierta = (p > 0.5) == y
    for col, nombres in (("surface", {}), ("tourney_level", NOMBRES_NIVEL)):
        for valor, g in pd.Series(acierta).groupby(prueba[col].to_numpy()):
            if len(g) >= 50:
                informe.append(f"  {nombres.get(valor, valor):<14} {g.mean():>6.1%}  ({len(g):,} partidos)")

    importancia = pd.Series(arbol.get_booster().get_score(importance_type="gain")).sort_values(ascending=False)
    informe.append("\nVariables más importantes de XGBoost (ganancia media):")
    for nombre, valor in importancia.head(15).items():
        informe.append(f"  {nombre:<36} {valor:>8.1f}")

    texto = "\n".join(informe)
    print(texto)
    (DATOS / "informe_modelo.txt").write_text(texto, encoding="utf-8")

    # Modelo final para la app: mismos parámetros y nº de árboles, entrenado con todos los partidos
    hasta = f["fecha"].max()
    entrenar_final(f, bases, arbol.best_iteration + 1, hasta).save_model(DATOS / "modelo_xgb.json")
    (DATOS / "modelo_columnas.json").write_text(json.dumps({
        "bases": bases, "columnas": columnas, "calibracion": round(calibracion, 4),
        # 08_seguimiento.py mide el acierto solo con los partidos posteriores a esta fecha
        "entrenado_hasta": f"{hasta:%Y-%m-%d}",
    }, indent=2))
    print(f"\nModelo final entrenado con {len(f):,} partidos y guardado en data/modelo_xgb.json")


if __name__ == "__main__":
    main()
