namespace TenisPredictor.Api.Models;

// Error que devuelve cualquier endpoint de la API: {"error": "..."}
public record ErrorApi(string Error);

// Sección "Datos" de appsettings.json: carpeta con tenis.db, modelo.onnx y modelo.json
public class DatosOptions
{
    public string Directorio { get; set; } = "Datos";
}

// Sección "Explicacion" de appsettings.json: la explicación del pronóstico escrita por Claude
public class ExplicacionOptions
{
    public string Modelo { get; set; } = "claude-opus-5-5";
    public int MaxTokens { get; set; } = 4000;
    public int PeticionesPorMinuto { get; set; } = 10;
    public int HorasCache { get; set; } = 24;
}

// --- Jugadores ---------------------------------------------------------------------------------

public record JugadorResumen(int Id, string Nombre, string? Pais, int? Ranking, double? Elo);

public record EloJugador(double? General, double? Dura, double? Tierra, double? Hierba, int? Partidos);

public record Jugador(
    int Id, string Nombre, string? Pais, string? Mano, int? Altura, string? FechaNacimiento,
    int? Ranking, int? PuntosRanking, EloJugador Elo, string? UltimoPartido, bool Activo);

// Estadísticas de una ventana: "52s" (últimas 52 semanas), "sup" (2 años en una superficie) o "carrera".
// Valores: aces_partido, pct_puntos_saque, pct_puntos_resto, dominance_ratio... (ver 04_features.py)
public record EstadisticasJugador(string Ventana, string Superficie, Dictionary<string, double?> Valores);

public record Partido(
    string Fecha, string Torneo, string Nivel, string? Superficie, string Ronda, string Marcador,
    int GanadorId, string GanadorNombre, int PerdedorId, string PerdedorNombre);

public record FichaJugador(Jugador Jugador, List<EstadisticasJugador> Estadisticas, List<Partido> UltimosPartidos);

public record PuntoElo(string Fecha, double Elo);

// --- Enfrentamiento directo ----------------------------------------------------------------------

public record BalanceH2H(int VictoriasA, int VictoriasB);

public record H2H(
    JugadorResumen JugadorA, JugadorResumen JugadorB, BalanceH2H Total,
    Dictionary<string, BalanceH2H> PorSuperficie, List<Partido> Partidos);

// --- Predicción ------------------------------------------------------------------------------------

// Superficie: Hard, Clay o Grass. Nivel: G (Grand Slam), M (Masters), A (ATP 250/500), F (Finals),
// D (Copa Davis), O (Juegos Olímpicos), C (Challenger). Ronda: R128, R64, R32, R16, QF, SF, F, RR...
public record PrediccionRequest(
    int JugadorA, int JugadorB, string Superficie, string Nivel = "A", string Ronda = "R32", int MejorDe = 3,
    bool VieneDePreviaA = false, bool VieneDePreviaB = false);

// Una fila de la comparativa que enseña el front: el valor de cada jugador en una estadística clave
public record Comparativa(string Clave, string Nombre, double? ValorA, double? ValorB, bool MayorEsMejor);

public record PrediccionResponse(
    JugadorResumen JugadorA, JugadorResumen JugadorB, double ProbabilidadA, double ProbabilidadB,
    double ProbabilidadEloA, BalanceH2H H2H, List<Comparativa> Comparativa, string FechaDatos);
