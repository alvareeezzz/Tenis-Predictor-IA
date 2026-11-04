# Tenis Predictor IA

App para pronosticar partidos de tenis ATP con datos reales: Elo (general y por superficie),
enfrentamientos directos y estadísticas individuales (aces, % de saque, puntos al resto, break points…).

- **data-pipeline/** (Python): descarga de datos, cálculo de Elo y estadísticas, entrenamiento del modelo
- **backend/** (ASP.NET Core .NET 10): API de jugadores, H2H y predicción *(pendiente)*
- **frontend/** (Angular 20): buscador, comparador y pronóstico *(pendiente)*

## Puesta en marcha

```bash
python -m venv .venv
.venv/Scripts/pip install -r data-pipeline/requirements.txt
.venv/Scripts/python data-pipeline/01_descargar.py   # descarga los CSV (~160 MB)
.venv/Scripts/python data-pipeline/02_limpiar.py     # une y ordena los partidos
.venv/Scripts/python data-pipeline/03_elo.py         # Elo general y por superficie
.venv/Scripts/python data-pipeline/04_features.py    # H2H, estadísticas de saque y resto, forma, cansancio
.venv/Scripts/python data-pipeline/05_entrenar.py    # entrena y evalúa los modelos (~5 min)
.venv/Scripts/python data-pipeline/06_exportar.py    # genera backend/Datos/tenis.db y modelo.onnx
```

`tenis.db` (~60 MB) y `modelo.onnx` no se suben al repositorio: se regeneran con los scripts.
`backend/Datos/modelo.json` describe el orden y el significado de las variables del modelo, y
`casos_prueba.json` trae predicciones de referencia para comprobar que el backend calcula lo mismo.

## Resultados (partidos del circuito principal 2023-2026, nunca vistos al entrenar)

| Modelo | Acierto | Log loss |
|---|---|---|
| Gana el mejor clasificado en el ranking ATP | 64,4 % | – |
| Elo combinado (general + superficie) | 65,9 % | 0,608 |
| Regresión logística | 67,1 % | 0,595 |
| **XGBoost** | **67,1 %** | **0,594** |

## Datos

Partidos, rankings y jugadores ATP recopilados por **Jeff Sackmann**
([tennis_atp](https://github.com/JeffSackmann/tennis_atp)), obtenidos del espejo de archivo
[Aneeshers/tennis-sackmann-archive](https://github.com/Aneeshers/tennis-sackmann-archive).
Licencia [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): uso no comercial.
Los datos no se guardan en este repositorio; se descargan con el script.
