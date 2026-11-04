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
.venv/Scripts/python data-pipeline/01_descargar.py
```

## Datos

Partidos, rankings y jugadores ATP recopilados por **Jeff Sackmann**
([tennis_atp](https://github.com/JeffSackmann/tennis_atp)), obtenidos del espejo de archivo
[Aneeshers/tennis-sackmann-archive](https://github.com/Aneeshers/tennis-sackmann-archive).
Licencia [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): uso no comercial.
Los datos no se guardan en este repositorio; se descargan con el script.
