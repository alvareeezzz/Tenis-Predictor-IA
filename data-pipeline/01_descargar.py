"""Fase 1 - Descarga los datos ATP de Jeff Sackmann y las cuotas de apuestas.

El repositorio original (JeffSackmann/tennis_atp) dejó de estar disponible en 2026,
así que se usa el espejo de archivo Aneeshers/tennis-sackmann-archive, con la misma
licencia: CC BY-NC-SA 4.0 (uso no comercial, citando a Jeff Sackmann).

Cuotas de Pinnacle desde 2021: Valuebetennis (CC BY 4.0, https://www.valuebetennis.com/donnees.htm),
ya emparejadas con los partidos de Sackmann por sumeetamin/tennis-history-data.
"""

from pathlib import Path

import requests

BASE_URL = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/atp"
DESTINO = Path(__file__).parent / "data" / "raw"
CUOTAS_URL = "https://raw.githubusercontent.com/sumeetamin/tennis-history-data/HEAD/data/odds/linked_odds_sackmann.csv.gz"
CUOTAS_DESTINO = DESTINO / "cuotas" / "linked_odds_sackmann.csv.gz"

# Se baja desde 1968 (inicio de la Era Open) para que el Elo tenga historia suficiente,
# aunque las estadísticas de cada partido (aces, % de saque...) solo existen desde 1991.
PRIMER_ANIO = 1968
PRIMER_ANIO_CHALLENGER = 1978
ULTIMO_ANIO = 2026

ARCHIVOS = (
    [f"atp_matches_{anio}.csv" for anio in range(PRIMER_ANIO, ULTIMO_ANIO + 1)]
    # Challengers y previas: muchos jugadores pasan ahí media temporada y el Elo los necesita.
    + [f"atp_matches_qual_chall_{anio}.csv" for anio in range(PRIMER_ANIO_CHALLENGER, ULTIMO_ANIO + 1)]
    + [
        "atp_players.csv",
        "atp_rankings_70s.csv",
        "atp_rankings_80s.csv",
        "atp_rankings_90s.csv",
        "atp_rankings_00s.csv",
        "atp_rankings_10s.csv",
        "atp_rankings_20s.csv",
        "atp_rankings_current.csv",
        "matches_data_dictionary.txt",
    ]
)


def descargar(nombre: str, forzar: bool = False) -> None:
    ruta = DESTINO / nombre
    if ruta.exists() and not forzar:
        return
    respuesta = requests.get(f"{BASE_URL}/{nombre}", timeout=60)
    respuesta.raise_for_status()
    ruta.write_bytes(respuesta.content)
    print(f"  {nombre} ({len(respuesta.content) / 1024:.0f} KB)")


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    print(f"Descargando {len(ARCHIVOS)} archivos en {DESTINO}")
    for nombre in ARCHIVOS:
        # El año en curso y el ranking actual se vuelven a bajar siempre por si hay cambios.
        forzar = str(ULTIMO_ANIO) in nombre or "current" in nombre
        descargar(nombre, forzar)

    # Las cuotas se actualizan a diario: se bajan siempre
    CUOTAS_DESTINO.parent.mkdir(exist_ok=True)
    respuesta = requests.get(CUOTAS_URL, timeout=120)
    respuesta.raise_for_status()
    CUOTAS_DESTINO.write_bytes(respuesta.content)
    print(f"  cuotas: {CUOTAS_DESTINO.name} ({len(respuesta.content) / 1024:.0f} KB)")
    print("Listo.")


if __name__ == "__main__":
    main()
