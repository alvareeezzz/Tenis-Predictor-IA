"""Fase 1 - Descarga los datos ATP de Jeff Sackmann, los resultados recientes y las cuotas.

El repositorio original (JeffSackmann/tennis_atp) dejó de estar disponible en 2026,
así que se usa el espejo de archivo Aneeshers/tennis-sackmann-archive, con la misma
licencia: CC BY-NC-SA 4.0 (uso no comercial, citando a Jeff Sackmann). Ese espejo es una
copia de junio de 2026 y ya no cambia.

Los resultados posteriores salen de Valuebetennis (CC BY 4.0, https://www.valuebetennis.com/donnees.htm),
ya unidos a los identificadores de Sackmann por sumeetamin/tennis-history-data (data/curated/post_2000),
que se actualiza a diario. De ahí salen también las cuotas de Pinnacle desde 2021.
"""

from datetime import date
from pathlib import Path

import requests

# Fijado a un commit: el espejo está congelado y así nadie puede cambiar lo que se descarga
BASE_URL = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/83733587353df8a41f2fd4f516147d5aa83f5a8d/atp"
DESTINO = Path(__file__).parent / "data" / "raw"
REPO_RECIENTES = "https://raw.githubusercontent.com/sumeetamin/tennis-history-data/HEAD/data"
CUOTAS_DESTINO = DESTINO / "cuotas" / "linked_odds_sackmann.csv.gz"
RECIENTES_DESTINO = DESTINO / "recientes"

# Se baja desde 1968 (inicio de la Era Open) para que el Elo tenga historia suficiente,
# aunque las estadísticas de cada partido (aces, % de saque...) solo existen desde 1991.
PRIMER_ANIO = 1968
PRIMER_ANIO_CHALLENGER = 1978
ULTIMO_ANIO = 2026  # último año del archivo de Sackmann
PRIMER_ANIO_RECIENTES = 2026  # desde aquí se completan los resultados con Valuebetennis

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


def bajar(url: str, ruta: Path) -> None:
    respuesta = requests.get(url, timeout=180)
    respuesta.raise_for_status()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(respuesta.content)
    print(f"  {ruta.name} ({len(respuesta.content) / 1024:.0f} KB)")


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    print(f"Descargando el archivo de Sackmann en {DESTINO}")
    for nombre in ARCHIVOS:
        # El espejo no cambia: solo se baja lo que falta
        if not (DESTINO / nombre).exists():
            bajar(f"{BASE_URL}/{nombre}", DESTINO / nombre)

    # Resultados recientes y cuotas: se actualizan a diario, se bajan siempre
    print("Descargando resultados recientes y cuotas")
    for anio in range(PRIMER_ANIO_RECIENTES, date.today().year + 1):
        bajar(f"{REPO_RECIENTES}/curated/post_2000/matches/atp_{anio}.csv.gz", RECIENTES_DESTINO / f"atp_{anio}.csv.gz")
    bajar(f"{REPO_RECIENTES}/odds/linked_odds_sackmann.csv.gz", CUOTAS_DESTINO)
    print("Listo.")


if __name__ == "__main__":
    main()
