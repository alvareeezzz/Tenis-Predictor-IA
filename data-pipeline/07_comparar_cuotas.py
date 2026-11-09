"""Compara el modelo con las cuotas de Pinnacle en los partidos de prueba (2023 en adelante).

Las probabilidades del modelo son las del entrenado SIN ver la prueba (05_entrenar.py guarda
data/predicciones_prueba.parquet). Las cuotas se convierten en probabilidad quitando el margen
de la casa: P(A) = (1/cuota_A) / (1/cuota_A + 1/cuota_B).

Cuotas: Valuebetennis (CC BY 4.0), emparejadas con Sackmann por sumeetamin/tennis-history-data.

Salida: data/informe_cuotas.txt
"""

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

DATOS = Path(__file__).parent / "data"
VENTAJA_MINIMA = 0.05  # en la simulación se apuesta si prob_modelo * cuota > 1 + VENTAJA_MINIMA


def normalizar(nombre: str) -> str:
    nombre = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", nombre)


def cargar_cuotas() -> pd.DataFrame:
    c = pd.read_csv(DATOS / "raw" / "cuotas" / "linked_odds_sackmann.csv.gz", low_memory=False)
    c = c[c["genre"] == "atp"]
    gana_1 = c["joueur1_id"] == c["vainqueur_id"]
    return pd.DataFrame({
        "tourney_date": c["sackmann_tourney_date"], "tourney_name": c["sackmann_tourney_name"],
        "round": c["sackmann_round"],
        "ganador": c["joueur1"].where(gana_1, c["joueur2"]).map(normalizar),
        "perdedor": c["joueur2"].where(gana_1, c["joueur1"]).map(normalizar),
        "cierre_ganador": c["cote1_cloture"].where(gana_1, c["cote2_cloture"]),
        "cierre_perdedor": c["cote2_cloture"].where(gana_1, c["cote1_cloture"]),
        "apertura_ganador": c["cote1_ouverture"].where(gana_1, c["cote2_ouverture"]),
        "apertura_perdedor": c["cote2_ouverture"].where(gana_1, c["cote1_ouverture"]),
    }).drop_duplicates(["tourney_date", "tourney_name", "round", "ganador", "perdedor"], keep=False)


def prob_mercado(cuota_a: pd.Series, cuota_b: pd.Series) -> pd.Series:
    validas = (cuota_a > 1) & (cuota_b > 1)
    inv_a, inv_b = 1 / cuota_a, 1 / cuota_b
    return (inv_a / (inv_a + inv_b)).where(validas)


def unir(predicciones: pd.DataFrame, partidos: pd.DataFrame, cuotas: pd.DataFrame) -> pd.DataFrame:
    p = predicciones.drop(columns="fecha", errors="ignore").merge(partidos[["orden", "fecha", "tourney_date", "tourney_name", "tourney_level", "round",
                                     "winner_name", "loser_name"]], on="orden")
    p["ganador"] = p["winner_name"].map(normalizar)
    p["perdedor"] = p["loser_name"].map(normalizar)
    p = p.merge(cuotas, on=["tourney_date", "tourney_name", "round", "ganador", "perdedor"], how="left")
    a_gana = p["gana_a"] == 1
    for momento in ("cierre", "apertura"):
        cuota_a = p[f"{momento}_ganador"].where(a_gana, p[f"{momento}_perdedor"])
        cuota_b = p[f"{momento}_perdedor"].where(a_gana, p[f"{momento}_ganador"])
        p[f"cuota_a_{momento}"], p[f"cuota_b_{momento}"] = cuota_a, cuota_b
        p[f"prob_{momento}"] = prob_mercado(cuota_a, cuota_b)
        p[f"margen_{momento}"] = 1 / cuota_a + 1 / cuota_b - 1
    return p


def metricas(y, p) -> tuple[float, float, float]:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return ((p > 0.5) == y).mean(), log_loss(y, p), ((p - y) ** 2).mean()


def simular_apuestas(d: pd.DataFrame, momento: str, rng: np.random.Generator) -> str:
    """Apuesta 1 unidad al jugador en el que el modelo ve ventaja frente a la cuota."""
    p_a, p_b = d["prob_xgb"], 1 - d["prob_xgb"]
    cuota_a, cuota_b = d[f"cuota_a_{momento}"], d[f"cuota_b_{momento}"]
    valor_a, valor_b = p_a * cuota_a - 1, p_b * cuota_b - 1
    apuesta_a = (valor_a > VENTAJA_MINIMA) & (valor_a >= valor_b)
    apuesta_b = (valor_b > VENTAJA_MINIMA) & (valor_b > valor_a)
    ganancia = np.where(apuesta_a, np.where(d["gana_a"] == 1, cuota_a - 1, -1.0), 0.0)
    ganancia = ganancia + np.where(apuesta_b, np.where(d["gana_a"] == 0, cuota_b - 1, -1.0), 0.0)
    ganancia = ganancia[(apuesta_a | apuesta_b).to_numpy()]
    if len(ganancia) == 0:
        return f"  {momento}: ninguna apuesta"
    roi = ganancia.mean()
    # Intervalo de confianza del 95 % por bootstrap: ¿el resultado se distingue de la suerte?
    remuestras = rng.choice(ganancia, size=(2000, len(ganancia)), replace=True).mean(axis=1)
    bajo, alto = np.percentile(remuestras, [2.5, 97.5])
    return (f"  Cuota de {momento:<9} {len(ganancia):>5,} apuestas | beneficio {ganancia.sum():>+8.1f} u | "
            f"ROI {roi:>+6.1%} (IC 95 %: {bajo:+.1%} a {alto:+.1%})")


def main() -> None:
    predicciones = pd.read_parquet(DATOS / "predicciones_prueba.parquet")
    partidos = pd.read_parquet(DATOS / "partidos.parquet")
    d = unir(predicciones, partidos, cargar_cuotas())
    informe = [f"Partidos de prueba (circuito principal 2023-2026): {len(d):,}",
               f"  con cuota de cierre de Pinnacle: {d['prob_cierre'].notna().sum():,} ({d['prob_cierre'].notna().mean():.0%})",
               f"  con cuota de apertura:           {d['prob_apertura'].notna().sum():,} ({d['prob_apertura'].notna().mean():.0%})",
               f"  margen medio de Pinnacle: {d['margen_cierre'].mean():.1%} al cierre, {d['margen_apertura'].mean():.1%} en apertura"]

    modelos = {"Elo combinado": "prob_elo", "Regresión logística": "prob_logistica", "XGBoost": "prob_xgb"}
    for momento in ("cierre", "apertura"):
        s = d[d[f"prob_{momento}"].notna()]
        y = s["gana_a"].to_numpy()
        informe.append(f"\nMISMOS {len(s):,} PARTIDOS, frente a la cuota de {momento.upper()}")
        informe.append(f"  {'Método':<26} {'Acierto':>8} {'Log loss':>9} {'Brier':>7}")
        for nombre, col in [*modelos.items(), (f"Pinnacle ({momento})", f"prob_{momento}")]:
            a, ll, b = metricas(y, s[col])
            informe.append(f"  {nombre:<26} {a:>8.1%} {ll:>9.4f} {b:>7.4f}")

    # ¿Quién acierta cuando modelo y mercado no coinciden en el favorito?
    s = d[d["prob_cierre"].notna()]
    discrepan = (s["prob_xgb"] > 0.5) != (s["prob_cierre"] > 0.5)
    acierta_modelo = ((s.loc[discrepan, "prob_xgb"] > 0.5) == (s.loc[discrepan, "gana_a"] == 1)).mean()
    informe.append(f"\nModelo y Pinnacle (cierre) eligen distinto favorito en {discrepan.sum():,} partidos "
                   f"({discrepan.mean():.1%}): acierta el modelo en el {acierta_modelo:.1%} y Pinnacle en el {1 - acierta_modelo:.1%}")

    # ¿Aporta el modelo algo que el mercado no tenga? Mezcla aprendida en 2023-2024 y medida en 2025-2026
    logit = lambda p: np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    for momento in ("apertura", "cierre"):
        s = d[d[f"prob_{momento}"].notna()]
        x = np.column_stack([logit(s[f"prob_{momento}"]), logit(s["prob_xgb"])])
        ajuste = (s["fecha"] < "2025-01-01").to_numpy()
        mezcla = LogisticRegression().fit(x[ajuste], s["gana_a"][ajuste])
        y = s["gana_a"][~ajuste].to_numpy()
        _, ll_mercado, _ = metricas(y, s[f"prob_{momento}"][~ajuste])
        _, ll_mezcla, _ = metricas(y, mezcla.predict_proba(x[~ajuste])[:, 1])
        informe.append(f"\nMezcla mercado ({momento}) + modelo, medida en 2025-2026 ({(~ajuste).sum():,} partidos):")
        informe.append(f"  pesos: mercado {mezcla.coef_[0][0]:.2f}, modelo {mezcla.coef_[0][1]:.2f}")
        informe.append(f"  log loss solo mercado {ll_mercado:.4f} | mezcla {ll_mezcla:.4f} "
                       f"({'mejora' if ll_mezcla < ll_mercado else 'no mejora'})")

    rng = np.random.default_rng(0)
    informe.append(f"\nSimulación: apostar 1 unidad cuando prob_modelo x cuota > {1 + VENTAJA_MINIMA:.2f}")
    for momento in ("apertura", "cierre"):
        informe.append(simular_apuestas(d[d[f"prob_{momento}"].notna()], momento, rng))

    texto = "\n".join(informe)
    print(texto)
    (DATOS / "informe_cuotas.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
