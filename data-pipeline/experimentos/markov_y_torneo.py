"""Experimento: ¿aportan las variables de Markov (mejora 5) y de torneo (mejora 6)?

Entrena el mismo XGBoost de 05_entrenar.py con y sin cada grupo de variables y compara en los
mismos partidos de validación (2021-2022) y prueba (2023-2026), con IC 95 % por bootstrap.

    python data-pipeline/experimentos/markov_y_torneo.py [markov|torneo|ambas]

Resultado (9 de noviembre de 2026): ninguna mejora en validación, y en la prueba todas las
diferencias están dentro del ruido (log loss de -0,0001 a -0,0005, IC 95 % cruzando el cero).
No se incorporan al modelo; ver la sección de resultados del README.
"""
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "data"


def importar(nombre):
    spec = importlib.util.spec_from_file_location(nombre[:-3], RAIZ / nombre)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


e = importar("05_entrenar.py")


# --- Mejora 5: cadena de Markov --------------------------------------------------------------------

def prob_juego(p):
    q = 1 - p
    return p**4 * (1 + 4 * q + 10 * q**2) + 20 * p**3 * q**3 * p**2 / (1 - 2 * p * q)


def prob_tiebreak(pa, pb):
    """A saca el primer punto; luego se alterna cada dos. pa: A gana punto sacando; pb: A gana punto restando."""
    P = {(0, 0): np.ones_like(pa)}
    gana = np.zeros_like(pa)
    for total in range(0, 12):
        for i in range(0, min(total, 6) + 1):
            j = total - i
            if j > 6 or (i, j) not in P:
                continue
            prob = P.pop((i, j))
            saca_a = ((total + 1) // 2) % 2 == 0
            p = pa if saca_a else pb
            for di, pp in ((1, p), (0, 1 - p)):
                ni, nj = i + di, j + 1 - di
                if ni == 7 and nj <= 5:
                    gana = gana + prob * pp
                elif nj == 7 and ni <= 5:
                    pass
                else:
                    P[(ni, nj)] = P.get((ni, nj), 0) + prob * pp
    empate = P.get((6, 6), 0)
    desde_empate = pa * pb / (pa * pb + (1 - pa) * (1 - pb))
    return gana + empate * desde_empate


def prob_set(ga, gb, tb):
    """A saca el primer juego. ga: A gana juego sacando; gb: A gana juego restando; tb: A gana tie-break."""
    P = {(0, 0): np.ones_like(ga)}
    gana = np.zeros_like(ga)
    for total in range(0, 12):
        for i in range(0, min(total, 6) + 1):
            j = total - i
            if j > 6 or (i, j) not in P:
                continue
            prob = P.pop((i, j))
            g = ga if total % 2 == 0 else gb
            for di, pp in ((1, g), (0, 1 - g)):
                ni, nj = i + di, j + 1 - di
                if (ni == 6 and nj <= 4) or ni == 7:
                    gana = gana + prob * pp
                elif (nj == 6 and ni <= 4) or nj == 7:
                    pass
                else:
                    P[(ni, nj)] = P.get((ni, nj), 0) + prob * pp
    # 5-5 → 7-5 / 6-6 / 5-7 ya tratados en el bucle (6-5 y 5-6 siguen); queda el 6-6
    return gana + P.get((6, 6), 0) * tb


def prob_partido_markov(saque_a, resto_a, saque_b, resto_b, media_saque, mejor_de):
    """Probabilidad de que A gane, a partir de % de puntos al saque y al resto (Barnett y Clarke)."""
    pa = np.clip(saque_a - resto_b + (1 - media_saque), 0.3, 0.95)        # A gana punto sacando
    pb = 1 - np.clip(saque_b - resto_a + (1 - media_saque), 0.3, 0.95)    # A gana punto restando
    ga, gb = prob_juego(pa), 1 - prob_juego(1 - pb)
    s_a_primero = prob_set(ga, gb, prob_tiebreak(pa, pb))
    s_b_primero = 1 - prob_set(1 - gb, 1 - ga, prob_tiebreak(1 - pb, 1 - pa))
    s = (s_a_primero + s_b_primero) / 2
    p3 = s**2 * (3 - 2 * s)
    p5 = s**3 * (1 + 3 * (1 - s) + 6 * (1 - s) ** 2)
    return np.where(mejor_de == 5, p5, p3)


def variables_markov(f, ventanas=("rec", "sup", "52s")):
    entrenamiento = f[f["fecha"] < e.FIN_ENTRENAMIENTO]
    media = pd.concat([entrenamiento["pct_puntos_saque_sup_a"], entrenamiento["pct_puntos_saque_sup_b"]]).groupby(
        pd.concat([entrenamiento["superficie_elo"]] * 2)).mean()
    m = f["superficie_elo"].map(media).to_numpy()
    nuevas = {}
    for v in ventanas:
        p = prob_partido_markov(f[f"pct_puntos_saque_{v}_a"].to_numpy(), f[f"pct_puntos_resto_{v}_a"].to_numpy(),
                                f[f"pct_puntos_saque_{v}_b"].to_numpy(), f[f"pct_puntos_resto_{v}_b"].to_numpy(),
                                m, f["best_of"].to_numpy())
        p = np.clip(p, 1e-4, 1 - 1e-4)
        logit = np.log(p / (1 - p))
        nuevas[f"markov_{v}_a"], nuevas[f"markov_{v}_b"] = logit, -logit
    return pd.DataFrame(nuevas, index=f.index), media


# --- Mejora 6: torneo --------------------------------------------------------------------------------

def variables_torneo(f):
    """Velocidad de la pista (aces de ediciones anteriores) e historial del jugador en el torneo."""
    p = pd.read_parquet(DATOS / "partidos.parquet")
    p = p[p["fuente"] == "sackmann"]
    stats = p[p["w_SvGms"].fillna(0) + p["l_SvGms"].fillna(0) > 0]
    ed = stats.groupby(["tourney_name", "fecha"]).agg(aces=("w_ace", "sum"), aces2=("l_ace", "sum"),
                                                       sv=("w_SvGms", "sum"), sv2=("l_SvGms", "sum"), sup=("superficie_elo", "first")).reset_index()
    ed["tasa"] = (ed["aces"] + ed["aces2"]) / (ed["sv"] + ed["sv2"])
    ed = ed.sort_values("fecha")
    # Media de las 5 ediciones ANTERIORES del mismo torneo, relativa a la media de su superficie ese año
    media_sup = ed.groupby([ed["fecha"].dt.year, "sup"])["tasa"].transform("mean")
    ed["relativa"] = ed["tasa"] / media_sup
    ed["velocidad"] = ed.groupby("tourney_name")["relativa"].transform(lambda s: s.shift().rolling(5, min_periods=1).mean())
    vel = ed.set_index(["tourney_name", "fecha"])["velocidad"]
    velocidad = pd.Series(vel.reindex(pd.MultiIndex.from_arrays([f["tourney_name"], f["fecha"]])).to_numpy(), index=f.index)

    # Historial del jugador en ediciones anteriores del torneo (no la actual)
    largo = pd.concat([
        p[["fecha", "tourney_name", "winner_id"]].set_axis(["fecha", "torneo", "jugador"], axis=1).assign(v=1),
        p[["fecha", "tourney_name", "loser_id"]].set_axis(["fecha", "torneo", "jugador"], axis=1).assign(v=0),
    ])
    por_edicion = largo.groupby(["jugador", "torneo", "fecha"])["v"].agg(["sum", "count"]).reset_index().sort_values("fecha")
    g = por_edicion.groupby(["jugador", "torneo"])
    por_edicion["v_prev"] = g["sum"].cumsum() - por_edicion["sum"]
    por_edicion["n_prev"] = g["count"].cumsum() - por_edicion["count"]
    clave = por_edicion.set_index(["jugador", "torneo", "fecha"])
    nuevas = {"velocidad_pista": velocidad}
    for s in "ab":
        idx = pd.MultiIndex.from_arrays([f[f"id_{s}"], f["tourney_name"], f["fecha"]])
        v = clave["v_prev"].reindex(idx).to_numpy()
        n = clave["n_prev"].reindex(idx).to_numpy()
        nuevas[f"torneo_partidos_{s}"] = n
        nuevas[f"torneo_pct_victorias_{s}"] = np.where(n >= 3, v / np.where(n > 0, n, 1), np.nan)
    return pd.DataFrame(nuevas, index=f.index)


# --- Entrenamiento y comparación ---------------------------------------------------------------------

def matriz(f, bases, extra_dif, extra_ctx, invertir=False):
    x = e.matriz(f, bases, invertir)
    a, b = ("b", "a") if invertir else ("a", "b")
    for c in extra_dif:
        x[f"dif_{c}"] = f[f"{c}_{a}"].to_numpy() - f[f"{c}_{b}"].to_numpy()
    for c in extra_ctx:
        x[c] = f[c].to_numpy()
    return x


def entrenar_y_predecir(f, bases, extra_dif, extra_ctx, ent, val, pru):
    x = pd.concat([matriz(ent, bases, extra_dif, extra_ctx), matriz(ent, bases, extra_dif, extra_ctx, True)], ignore_index=True)
    y = np.concatenate([ent["gana_a"], 1 - ent["gana_a"]])
    xv = pd.concat([matriz(val, bases, extra_dif, extra_ctx), matriz(val, bases, extra_dif, extra_ctx, True)], ignore_index=True)
    yv = np.concatenate([val["gana_a"], 1 - val["gana_a"]])
    m = xgb.XGBClassifier(**e.PARAMETROS_XGB)
    m.fit(x, y, sample_weight=e.pesos(ent, e.FIN_ENTRENAMIENTO), eval_set=[(xv, yv)], verbose=False)
    pred = lambda d: (m.predict_proba(matriz(d, bases, extra_dif, extra_ctx))[:, 1]
                      + 1 - m.predict_proba(matriz(d, bases, extra_dif, extra_ctx, True))[:, 1]) / 2
    return pred(val), pred(pru), m


def perdida(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def comparar(nombre, y, p0, p1, rng):
    l0, l1 = perdida(y, p0), perdida(y, p1)
    d = l1 - l0
    boot = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
    return (f"  {nombre:<10} acierto {((p0 > .5) == y).mean():.2%} → {((p1 > .5) == y).mean():.2%} | "
            f"log loss {l0.mean():.4f} → {l1.mean():.4f} ({d.mean():+.4f}, IC95 {np.percentile(boot, 2.5):+.4f} a {np.percentile(boot, 97.5):+.4f})")


def main():
    t = time.time()
    f = e.preparar(pd.read_parquet(DATOS / "features.parquet"))
    f = f[f["final_normal"]].reset_index(drop=True)
    bases = e.variables_jugador(f)
    mk, media = variables_markov(f)
    to = variables_torneo(f)
    f = pd.concat([f, mk, to], axis=1)
    print(f"Media de puntos al saque por superficie: {media.round(3).to_dict()}")
    print(f"Cobertura markov_rec {mk['markov_rec_a'].notna().mean():.0%}, velocidad {to['velocidad_pista'].notna().mean():.0%}, "
          f"historial {to['torneo_pct_victorias_a'].notna().mean():.0%}  ({time.time() - t:.0f} s)")

    ent = f[f["fecha"] < e.FIN_ENTRENAMIENTO]
    val = f[(f["fecha"] >= e.FIN_ENTRENAMIENTO) & (f["fecha"] < e.FIN_VALIDACION) & f["circuito_principal"]]
    pru = f[(f["fecha"] >= e.FIN_VALIDACION) & f["circuito_principal"]]
    yv, yp = val["gana_a"].to_numpy(), pru["gana_a"].to_numpy()

    # Markov sola, sin modelo: ¿cuánto predice por sí misma?
    for v in ("rec", "sup", "52s"):
        col = f"markov_{v}_a"
        ok = pru[col].notna().to_numpy()
        pp = 1 / (1 + np.exp(-pru.loc[ok, col].to_numpy()))
        print(f"Markov {v} sola en prueba ({ok.sum():,}): acierto {((pp > .5) == yp[ok]).mean():.1%}, log loss {perdida(yp[ok], pp).mean():.4f}")

    experimentos = {
        "base": ([], []),
        "markov": (["markov_rec", "markov_sup", "markov_52s"], []),
        "torneo": (["torneo_pct_victorias", "torneo_partidos"], ["velocidad_pista"]),
        "ambas": (["markov_rec", "markov_sup", "markov_52s", "torneo_pct_victorias", "torneo_partidos"], ["velocidad_pista"]),
    }
    solo = sys.argv[1:] or list(experimentos)
    resultados = {}
    rng = np.random.default_rng(0)
    for nombre in ["base", *[n for n in solo if n != "base"]]:
        t = time.time()
        pv, pp, m = entrenar_y_predecir(f, bases, *experimentos[nombre], ent, val, pru)
        resultados[nombre] = (pv, pp)
        print(f"\n{nombre}: {m.best_iteration + 1} árboles ({time.time() - t:.0f} s)")
        if nombre != "base":
            print(comparar("validación", yv, resultados["base"][0], pv, rng))
            print(comparar("prueba", yp, resultados["base"][1], pp, rng))
            imp = pd.Series(m.get_booster().get_score(importance_type="gain")).sort_values(ascending=False)
            print("  importancia:", {k: round(v, 1) for k, v in imp.items() if "markov" in k or "torneo" in k or "velocidad" in k})


if __name__ == "__main__":
    main()
