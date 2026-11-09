# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Pronósticos de partidos ATP: un pipeline de Python calcula Elo, estadísticas y un modelo XGBoost; una
API ASP.NET Core (.NET 10) lo sirve con ONNX y una web Angular 20 lo enseña. Todo el proyecto (código,
identificadores, comentarios, textos de la web y commits) está en español.

## Comandos

```bash
# Python (desde la raíz; el entorno virtual está en .venv)
python -m venv .venv && .venv/Scripts/pip install -r data-pipeline/requirements.txt
.venv/Scripts/python data-pipeline/actualizar.py                # 01→02→03→04→06→08, ~2 min
.venv/Scripts/python data-pipeline/actualizar.py --reentrenar   # añade 05 (entrenar, ~5 min) y 07 (cuotas)
.venv/Scripts/python data-pipeline/04_features.py               # cualquier paso suelto, en orden
.venv/Scripts/python data-pipeline/08_seguimiento.py --desde 2026-06-02   # acierto simulando entrenar hasta esa fecha

# API (http://localhost:5014; sirve también la web compilada en backend/wwwroot)
cd backend && dotnet run
cd tests && dotnet test
cd tests && dotnet test --filter "FullyQualifiedName~DaLaMismaProbabilidadQuePython"   # un test

# Web
cd frontend && npx ng serve    # :4200, con proxy de /api a :5014
cd frontend && npx ng build    # compila en ../backend/wwwroot

# Clave de Anthropic para la explicación con IA (fase 9)
cd backend && dotnet user-secrets set "Anthropic:ApiKey" "sk-ant-..."   # o ANTHROPIC_API_KEY
```

En Windows con PYTHONIOENCODING sin fijar, los scripts que imprimen "→" o tildes fallan: usar
`PYTHONIOENCODING=utf-8`. **Para la API antes de `06_exportar.py` o `actualizar.py`**: tiene abierta
`tenis.db` y Windows no deja reemplazarla (`actualizar.py` lo comprueba por el puerto 5014). La API
carga modelo y estado de los jugadores al arrancar: hay que reiniciarla tras exportar.

## Arquitectura

**Flujo de datos.** `data-pipeline/data/` (ignorado en git) guarda los CSV descargados y los parquet
intermedios: `partidos` → `partidos_elo` → `features` → modelo. `06_exportar.py` escribe en
`backend/Datos/`: `tenis.db` y `modelo.onnx` (generados, ignorados) más `modelo.json` y
`casos_prueba.json` (versionados). Ningún dato se sube al repositorio: se regeneran con los scripts.

**Contrato Python ↔ .NET.** Las variables del modelo son diferencias A − B más contexto del partido,
construidas en `matriz()` de `05_entrenar.py`. `backend/Services/PrediccionService.cs` las reconstruye
en el orden de `modelo.json` (`Variables()`), calcula el H2H en SQLite y aplica lo mismo que Python:
predecir en los dos órdenes de jugadores, promediar y calibrar. Cualquier cambio en variables, en el
Elo combinado o en la calibración obliga a tocar ambos lados y a regenerar `casos_prueba.json`
(reentrenar → exportar); `PrediccionTests` compara .NET con esas predicciones de Python. Constantes
duplicadas que deben coincidir: `PESO_SUPERFICIE` (03 y 05), `ORDEN_RONDAS` (02 y 05), la fórmula de
`calibrar()` (05, 06 y `Calibrar()` en C#).

**Sin fuga de datos.** Cada variable usa solo partidos anteriores: `suma_previa()` y `suma_decaida()`
de `04_features.py` trabajan con sumas acumuladas por grupo y excluyen la fila actual.
`verificar_sin_fuga()` recalcula muestras a mano en cada ejecución y aborta si hay diferencias. El
"estado actual" de cada jugador para la app (`06_exportar.py`) reutiliza esas mismas funciones
añadiendo un partido virtual por jugador y superficie en la fecha de los datos. Los jugadores se
asignan a A/B al azar (semilla fija) porque en los CSV el ganador va siempre primero, y al entrenar
cada partido se añade también invertido.

**Cómo se decide si algo mejora.** Entrenamiento 1991-2020, validación 2021-2022 (para decidir),
prueba 2023 en adelante solo circuito principal (para informar). Las comparaciones se hacen en los
mismos partidos con IC 95 % por bootstrap de la diferencia de log loss. Lo probado sin éxito queda
en `data-pipeline/experimentos/` y en la lista "Qué se ha probado" del README (Markov, contexto de
torneo, estadísticas ajustadas por rival, ranking ATP como variable, ensamblados): conviene revisarla
antes de proponer otra mejora. La calibración se ajusta con los dos últimos años de predicciones
fuera de muestra porque el exceso de confianza aparece desde 2023.

**Elo** (`03_elo.py`): se calcula con circuito principal, Challengers y previas, pero se evalúa
solo en el circuito principal. Tiene en cuenta el marcador y la inactividad; los parámetros salen de
Optuna. La moqueta se acumula con pista dura.

## Particularidades de los datos

- **Sackmann** (`atp_matches_*.csv`) viene de un espejo congelado en junio de 2026: el repositorio
  original desapareció. `tourney_date` es el inicio del torneo, así que el orden dentro de un torneo lo
  da la ronda. Ojo: "QF" empieza por "Q"; las previas se detectan con `isin(["Q1".."Q4"])`.
- **Resultados posteriores**: `data/curated/post_2000` de sumeetamin/tennis-history-data
  (Valuebetennis), unidos en `cargar_recientes()` de `02_limpiar.py` (columna `fuente`). No traen
  ronda (`round = ""`), estadísticas de saque ni minutos (se estiman por juegos, solo en esas filas,
  para no alterar lo que vio el modelo). Los jugadores sin identidad verificada se emparejan por
  nombre y último apellido solo si coinciden con un único jugador activo.
- **El ranking ATP está congelado en el del 8 de junio de 2026**: no hay fuente abierta posterior.
  Por eso no es variable del modelo, aunque la web lo enseña.
- Las variables de cansancio e inactividad solo son reales con datos de menos de 14 días
  (`DIAS_DATOS_FRESCOS` en `06_exportar.py`); con datos más viejos se ponen neutras para todos.
- **No usar tennis-data.co.uk**: sus condiciones prohíben usar los datos para entrenar modelos (y
  bloquea descargas automáticas). Las cuotas vienen de Valuebetennis (CC BY 4.0).
- Los datos de Sackmann son CC BY-NC-SA 4.0 (uso no comercial); el pie de la web cita las fuentes.

## Backend y web

- La API sigue el estilo del portfolio del autor: *minimal APIs* en `Program.cs`, `Services/` y
  `Models/` con records, errores como `ErrorApi`. `/api/prediccion` y `/api/explicacion` comparten
  `Validar()`.
- `ExplicacionService` llama a Claude (`claude-opus-5-5`, esfuerzo bajo, `fallbacks` del servidor)
  solo con los datos que enseña la página y devuelve el texto en streaming (`TextoEnStreaming`). Sin
  clave responde 503; los tests fuerzan ese caso con `UseSetting("Anthropic:ApiKey", "")`.
- Web: el color de toda la app sigue a la superficie elegida (`data-superficie` en `<html>`, tokens
  en `src/styles.css`); tipografía Archivo en dos anchuras (estrecha para nombres y cifras). El estado
  del partido elegido vive en `PronosticoEstado` para no perderse al navegar.

## Actualización programada

La tarea "Tenis Predictor - actualización semanal" del Programador de tareas de Windows ejecuta
`data-pipeline/actualizacion_programada.ps1` los lunes a las 8:30 (o en cuanto se encienda el
ordenador si estaba apagado): para la API si está arrancada, ejecuta `actualizar.py` y la vuelve a
arrancar. Registro en `data-pipeline/data/actualizacion_programada.log` (y `api.log` para la API).
El `.ps1` va en UTF-8 con BOM: PowerShell 5.1 lee los que no lo llevan como ANSI.
