# Tenis Predictor IA

App para pronosticar partidos de tenis ATP con datos reales: Elo (general y por superficie),
enfrentamientos directos y estadísticas individuales (aces, % de saque, puntos al resto, break points…).

- **data-pipeline/** (Python): descarga de datos, cálculo de Elo y estadísticas, entrenamiento del modelo
- **backend/** (ASP.NET Core .NET 10): API de jugadores, H2H y predicción con el modelo ONNX
- **tests/** (xUnit): comprueba que la API da la misma probabilidad que Python
- **frontend/** (Angular 20): pronóstico sobre una pista, ficha de cada jugador y ranking Elo

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

## API

```bash
cd backend && dotnet run      # http://localhost:5014
cd tests && dotnet test       # 27 tests, incluidas las predicciones de referencia de Python
```

| Endpoint | Qué devuelve |
|---|---|
| `GET /api/info` | Fecha de los datos, fuente e informe del modelo |
| `GET /api/jugadores?buscar=alcaraz` | Jugadores activos cuyo nombre contiene el texto |
| `GET /api/jugadores/top?superficie=Clay` | Mejores por Elo (General, Hard, Clay o Grass) |
| `GET /api/jugadores/{id}` | Ficha: datos, Elo, estadísticas por ventana y superficie, últimos partidos |
| `GET /api/jugadores/{id}/elo` | Evolución del Elo partido a partido |
| `GET /api/h2h/{a}/{b}` | Enfrentamiento directo: balance total, por superficie y lista de partidos |
| `POST /api/prediccion` | Probabilidad de cada jugador, la del Elo, el H2H y una comparativa de estadísticas |
| `POST /api/explicacion` | El mismo partido explicado en un párrafo por Claude (texto que llega a trozos) |

Ejemplos listos para lanzar en [backend/TenisPredictor.Api.http](backend/TenisPredictor.Api.http).
En desarrollo, el esquema OpenAPI está en `/openapi/v1.json`.

### Explicación con IA

La web tiene un botón «Explicar este pronóstico»: el servidor recalcula el pronóstico y le pasa a
Claude (`claude-opus-5-5`, esfuerzo bajo) solo los datos de la página (probabilidades, Elo,
enfrentamientos, estadísticas, forma y descanso). Claude escribe un párrafo con quién es favorito,
por qué, y qué dato pide cautela. Solo se llama al pulsar el botón; la misma explicación se guarda
24 h y hay un límite de 10 por minuto por visitante (sección `Explicacion` de `appsettings.json`).
Si Claude rechaza una petición por seguridad, la API la reintenta con otro modelo (`fallbacks`).

La clave de la API de Anthropic se guarda fuera del repositorio:

```bash
cd backend
dotnet user-secrets set "Anthropic:ApiKey" "sk-ant-..."   # desarrollo
# en producción: variable de entorno ANTHROPIC_API_KEY
```

Sin clave, todo lo demás funciona y el botón avisa de que la explicación no está configurada.

## Web

```bash
cd frontend && npm install
npx ng serve                  # http://localhost:4200, con la API arrancada (redirige /api a :5014)
npx ng build                  # compila en backend/wwwroot: la API sirve la web en http://localhost:5014
```

- **Pronóstico**: elige jugador, rival, superficie, torneo y ronda. La probabilidad se dibuja como
  una pista: la red se coloca donde cae el pronóstico. Debajo, las estadísticas cara a cara y los
  enfrentamientos entre ambos. El color de la app sigue a la superficie (dura, tierra o hierba).
  Con la clave configurada, Claude explica el pronóstico en un párrafo.
- **Ficha del jugador**: Elo por superficie, su evolución, estadísticas de saque y resto por
  superficie y últimos partidos.
- **Ranking Elo**: los 50 mejores por Elo, en general o en cada superficie.

## Resultados (partidos del circuito principal 2023-2026, nunca vistos al entrenar)

11.024 partidos, incluidos los recientes de Valuebetennis (sin estadísticas de saque).

| Modelo | Acierto | Log loss |
|---|---|---|
| Gana el mejor clasificado en el ranking ATP | 64,1 % | – |
| Elo combinado (general + superficie) | 66,1 % | 0,607 |
| Regresión logística | 66,7 % | 0,596 |
| **XGBoost** | **66,7 %** | **0,597** |

### Acierto real, en las condiciones de la app (`08_seguimiento.py`)

Modelo entrenado solo con los partidos anteriores al 2 de junio de 2026 y evaluado con los 947 del
circuito principal jugados hasta noviembre, sin estadísticas de saque y con el ranking congelado:

| Método | Acierto | Log loss |
|---|---|---|
| Elo combinado | 64,4 % | 0,623 |
| **Modelo (la app)** | **64,7 %** | **0,617** |
| Pinnacle, apertura | 66,6 % | 0,601 |
| Pinnacle, cierre | 66,7 % | 0,602 |

Es un periodo más imprevisible para todos (Pinnacle también baja del 69 % al 67 %); la distancia con
el mercado se mantiene en unos 2 puntos. Con 947 partidos, el margen de error es de ±1,6 puntos.
`actualizar.py` repite la medición cada semana con los partidos posteriores al último entrenamiento
y la añade a `data/seguimiento.csv`.

### Calibración

Desde 2023 el modelo se pasaba de confiado (decía 85 % y el favorito ganaba el 81 %). Se corrige con
un solo parámetro ajustado con los dos últimos años: ajustado con 2023-2024 y medido en 2025-2026, el
log loss baja de 0,5997 a 0,5992 (IC 95 %: entre −0,0011 y −0,0001), y en el seguimiento de junio a
noviembre de 0,6187 a 0,6170. No cambia el acierto: solo hace más fiables los porcentajes.

Qué se ha probado para subir el acierto (decidido siempre en validación, 2021-2022):
- **Elo con marcador, inactividad y peso por superficie**, parámetros ajustados con Optuna: el Elo
  solo pasa de 65,8 % a 66,1 % en la prueba (log loss −0,0026 ± 0,0005, mejora real).
- **Estadísticas con decaimiento (`_rec`) y ajustadas por rival (`exceso_*`)**, hiperparámetros de
  XGBoost con Optuna y pesos por antigüedad: mejoran en validación (0,5831 → 0,5815), pero en la
  prueba el XGBoost queda igual que antes (66,9 % → 66,7 %, log loss +0,0006 ± 0,0008: ruido).
- **Sin ranking ATP**: no aporta nada sobre el Elo y en la app está congelado en junio de 2026.
- Promediar XGBoost y regresión logística: −0,0002 en validación, no compensa ejecutar dos modelos.
- **Modelo punto a punto (cadena de Markov)**: probabilidad del partido a partir de los puntos al
  saque y al resto de cada uno. Sola acierta el 60 %; como variable, nada en validación y −0,0001 en
  la prueba (ruido). Lo que sabe ya lo saben el Elo y las estadísticas de puntos.
- **Contexto del torneo**: velocidad de la pista (aces de ediciones anteriores) e historial del
  jugador en el torneo. −0,0001 en validación y −0,0004 en la prueba (IC 95 % cruzando el cero).
  No compensa añadir a la app la elección del torneo concreto.
  Ver [experimentos/markov_y_torneo.py](data-pipeline/experimentos/markov_y_torneo.py).

### Frente a las casas de apuestas (`07_comparar_cuotas.py`)

En los 6.416 partidos de prueba con cuota de Pinnacle (la casa más afinada del mercado):

| Método | Acierto | Log loss |
|---|---|---|
| XGBoost | 66,9 % | 0,596 |
| Pinnacle, cuota de apertura | 68,1 % | 0,586 |
| Pinnacle, cuota de cierre | 69,0 % | 0,582 |

El mercado sigue por delante y apostar con el modelo pierde dinero (ROI de -8 % a -10 %).
Cuotas: [Valuebetennis](https://www.valuebetennis.com/donnees.htm) (CC BY 4.0), emparejadas con
Sackmann por [sumeetamin/tennis-history-data](https://github.com/sumeetamin/tennis-history-data).

## Datos

Partidos, rankings y jugadores ATP recopilados por **Jeff Sackmann**
([tennis_atp](https://github.com/JeffSackmann/tennis_atp)), obtenidos del espejo de archivo
[Aneeshers/tennis-sackmann-archive](https://github.com/Aneeshers/tennis-sackmann-archive).
Licencia [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): uso no comercial.
Los datos no se guardan en este repositorio; se descargan con el script.

El archivo de Sackmann llega hasta junio de 2026. Los resultados posteriores son de
[Valuebetennis](https://www.valuebetennis.com/donnees.htm) (CC BY 4.0), ya unidos a los jugadores de
Sackmann por [sumeetamin/tennis-history-data](https://github.com/sumeetamin/tennis-history-data),
que se actualiza a diario. Esos resultados no traen estadísticas de saque ni duración (se estima por
los juegos jugados), y solo cubren parte de los partidos de cada torneo.

## Actualización semanal

```bash
# con la API parada (tiene abierta tenis.db)
.venv/Scripts/python data-pipeline/actualizar.py               # ~2 min: resultados nuevos, Elo, estadísticas, exportación
.venv/Scripts/python data-pipeline/actualizar.py --reentrenar  # cada pocos meses: también reentrena y compara con las cuotas
```

Con datos de menos de 14 días, el pronóstico tiene en cuenta el cansancio y la inactividad reales
de cada jugador; con datos más viejos, esas variables se ponen iguales para todos.

**Pendiente:** programar `actualizar.py` para que se ejecute solo cada semana (Programador de
tareas de Windows, por ejemplo los lunes a las 8:00, con la API parada a esa hora).

Limitaciones conocidas:
- El ranking ATP se queda en el del 8 de junio de 2026: ninguna fuente abierta publica los posteriores.
  El Elo, que es lo que más pesa en el modelo, sí se actualiza con cada resultado.
- Los jugadores de los resultados recientes sin identidad verificada se emparejan por nombre y
  apellido solo si coinciden con un único jugador activo; si no, el partido se descarta.
