"""Fase 8 - Actualización semanal: resultados nuevos → Elo → estadísticas → base de datos de la app.

    python data-pipeline/actualizar.py               # cada semana (~3 min)
    python data-pipeline/actualizar.py --reentrenar  # cada pocos meses: también reentrena el modelo
                                                     # y lo vuelve a comparar con las cuotas (~10 min)

La API carga los datos al arrancar: hay que pararla antes y arrancarla después.
"""

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).parent
PUERTO_API = 5014

PASOS = [
    ("01_descargar.py", "Descargar resultados nuevos y cuotas"),
    ("02_limpiar.py", "Unir y ordenar los partidos"),
    ("03_elo.py", "Recalcular el Elo"),
    ("04_features.py", "Recalcular estadísticas, forma y H2H"),
]
PASOS_REENTRENAR = [
    ("05_entrenar.py", "Reentrenar el modelo"),
    ("07_comparar_cuotas.py", "Comparar con las cuotas de Pinnacle"),
]
PASO_EXPORTAR = ("06_exportar.py", "Exportar tenis.db y modelo.onnx para la API")
# Acierto con los partidos jugados después de entrenar el modelo. No es imprescindible: justo
# después de reentrenar todavía no hay partidos nuevos suficientes y solo avisa.
PASO_SEGUIMIENTO = ("08_seguimiento.py", "Medir el acierto con los partidos nuevos")


def api_arrancada() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PUERTO_API)) == 0


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argumentos.add_argument("--reentrenar", action="store_true", help="reentrena el modelo y lo compara con las cuotas")
    reentrenar = argumentos.parse_args().reentrenar

    if api_arrancada():
        sys.exit(f"La API está arrancada en el puerto {PUERTO_API} y tiene abierta tenis.db: párala antes de actualizar.")

    pasos = PASOS + (PASOS_REENTRENAR if reentrenar else []) + [PASO_EXPORTAR, PASO_SEGUIMIENTO]
    inicio = time.time()
    for i, (script, descripcion) in enumerate(pasos, 1):
        print(f"\n[{i}/{len(pasos)}] {descripcion}", flush=True)
        t = time.time()
        resultado = subprocess.run([sys.executable, str(RAIZ / script)])
        if resultado.returncode != 0:
            if (script, descripcion) == PASO_SEGUIMIENTO:
                print("      Seguimiento omitido: la app ya está actualizada.")
                continue
            sys.exit(f"Ha fallado {script}: los datos de la API no se han tocado.")
        print(f"      {time.time() - t:.0f} s", flush=True)
    print(f"\nActualización terminada en {(time.time() - inicio) / 60:.1f} min. Arranca la API: cd backend && dotnet run")


if __name__ == "__main__":
    main()
