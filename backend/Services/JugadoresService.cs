using Microsoft.Data.Sqlite;
using TenisPredictor.Api.Models;

namespace TenisPredictor.Api.Services;

public class JugadoresService(BaseDatos bd)
{
    // Columna de Elo de cada superficie; también sirve de lista blanca para no meter texto libre en el SQL
    private static readonly Dictionary<string, string> ColumnaElo = new(StringComparer.OrdinalIgnoreCase)
    {
        ["General"] = "elo",
        ["Hard"] = "elo_hard",
        ["Clay"] = "elo_clay",
        ["Grass"] = "elo_grass"
    };

    public static bool EsSuperficieValida(string superficie) => ColumnaElo.ContainsKey(superficie);

    private const string SqlPartidos = """
        SELECT p.fecha, p.torneo, p.nivel, p.superficie, p.ronda, p.marcador,
               p.ganador_id, g.nombre AS ganador, p.perdedor_id, l.nombre AS perdedor
        FROM partidos p
        JOIN jugadores g ON g.player_id = p.ganador_id
        JOIN jugadores l ON l.player_id = p.perdedor_id
        """;

    // Solo jugadores activos (con partidos en los 2 últimos años): son los que se pueden pronosticar
    public List<JugadorResumen> Buscar(string texto, int limite)
    {
        using var conexion = bd.Abrir();
        using var comando = BaseDatos.Comando(conexion, """
            SELECT player_id, nombre, pais, rank, elo FROM jugadores
            WHERE activo = 1 AND nombre LIKE $texto
            ORDER BY rank IS NULL, rank, elo DESC
            LIMIT $limite
            """, ("$texto", $"%{texto.Trim()}%"), ("$limite", limite));
        return LeerResumenes(comando);
    }

    public List<JugadorResumen> MejoresPorElo(string superficie, int limite)
    {
        var columna = ColumnaElo[superficie];
        using var conexion = bd.Abrir();
        using var comando = BaseDatos.Comando(conexion, $"""
            SELECT player_id, nombre, pais, rank, {columna} AS elo FROM jugadores
            WHERE activo = 1 AND ultimo_partido >= date((SELECT max(ultimo_partido) FROM jugadores), '-365 days')
            ORDER BY {columna} DESC
            LIMIT $limite
            """, ("$limite", limite));
        return LeerResumenes(comando);
    }

    public JugadorResumen? Resumen(int id)
    {
        using var conexion = bd.Abrir();
        using var comando = BaseDatos.Comando(conexion,
            "SELECT player_id, nombre, pais, rank, elo FROM jugadores WHERE player_id = $id", ("$id", id));
        return LeerResumenes(comando).FirstOrDefault();
    }

    public FichaJugador? Ficha(int id)
    {
        using var conexion = bd.Abrir();
        Jugador? jugador;
        using (var comando = BaseDatos.Comando(conexion, "SELECT * FROM jugadores WHERE player_id = $id", ("$id", id)))
        using (var r = comando.ExecuteReader())
        {
            if (!r.Read()) return null;
            jugador = new Jugador(
                id, r.GetString(r.GetOrdinal("nombre")), BaseDatos.Texto(r, "pais"), BaseDatos.Texto(r, "mano"),
                BaseDatos.Entero(r, "altura"), BaseDatos.Texto(r, "fecha_nacimiento"),
                BaseDatos.Entero(r, "rank"), BaseDatos.Entero(r, "puntos_rank"),
                new EloJugador(Redondear(BaseDatos.Numero(r, "elo")), Redondear(BaseDatos.Numero(r, "elo_hard")),
                               Redondear(BaseDatos.Numero(r, "elo_clay")), Redondear(BaseDatos.Numero(r, "elo_grass")),
                               BaseDatos.Entero(r, "partidos_elo")),
                BaseDatos.Texto(r, "ultimo_partido"), BaseDatos.Entero(r, "activo") == 1);
        }

        var estadisticas = new List<EstadisticasJugador>();
        using (var comando = BaseDatos.Comando(conexion, "SELECT * FROM estadisticas WHERE player_id = $id", ("$id", id)))
        using (var r = comando.ExecuteReader())
        {
            while (r.Read())
            {
                var valores = new Dictionary<string, double?>();
                for (var i = 0; i < r.FieldCount; i++)
                    if (r.GetName(i) is not ("player_id" or "ventana" or "superficie"))
                        valores[r.GetName(i)] = BaseDatos.Numero(r, r.GetName(i));
                estadisticas.Add(new EstadisticasJugador(r.GetString(r.GetOrdinal("ventana")), r.GetString(r.GetOrdinal("superficie")), valores));
            }
        }

        using var ultimos = BaseDatos.Comando(conexion,
            $"{SqlPartidos} WHERE p.ganador_id = $id OR p.perdedor_id = $id ORDER BY p.orden DESC LIMIT 10", ("$id", id));
        return new FichaJugador(jugador, estadisticas, LeerPartidos(ultimos));
    }

    // Elo antes de cada partido: la evolución de su nivel a lo largo de la carrera
    public List<PuntoElo> HistorialElo(int id)
    {
        using var conexion = bd.Abrir();
        using var comando = BaseDatos.Comando(conexion, """
            SELECT fecha, CASE WHEN ganador_id = $id THEN elo_ganador ELSE elo_perdedor END AS elo
            FROM partidos WHERE ganador_id = $id OR perdedor_id = $id ORDER BY orden
            """, ("$id", id));
        using var r = comando.ExecuteReader();
        var puntos = new List<PuntoElo>();
        while (r.Read())
            puntos.Add(new PuntoElo(r.GetString(0), Math.Round(r.GetDouble(1), 1)));
        return puntos;
    }

    public H2H? EnfrentamientoDirecto(int a, int b)
    {
        if (Resumen(a) is not { } jugadorA || Resumen(b) is not { } jugadorB) return null;
        using var conexion = bd.Abrir();
        using var comando = BaseDatos.Comando(conexion, $"""
            {SqlPartidos}
            WHERE (p.ganador_id = $a AND p.perdedor_id = $b) OR (p.ganador_id = $b AND p.perdedor_id = $a)
            ORDER BY p.orden DESC
            """, ("$a", a), ("$b", b));
        var partidos = LeerPartidos(comando);

        static BalanceH2H Balance(IEnumerable<Partido> ps, int a) =>
            new(ps.Count(p => p.GanadorId == a), ps.Count(p => p.GanadorId != a));
        var porSuperficie = partidos.Where(p => p.Superficie is not null)
            .GroupBy(p => p.Superficie!)
            .ToDictionary(g => g.Key, g => Balance(g, a));
        return new H2H(jugadorA, jugadorB, Balance(partidos, a), porSuperficie, partidos);
    }

    private static double? Redondear(double? elo) => elo is { } e ? Math.Round(e, 1) : null;

    private static List<JugadorResumen> LeerResumenes(SqliteCommand comando)
    {
        using var r = comando.ExecuteReader();
        var lista = new List<JugadorResumen>();
        while (r.Read())
            lista.Add(new JugadorResumen(r.GetInt32(0), r.GetString(1), BaseDatos.Texto(r, "pais"),
                                         BaseDatos.Entero(r, "rank"), Redondear(BaseDatos.Numero(r, "elo"))));
        return lista;
    }

    private static List<Partido> LeerPartidos(SqliteCommand comando)
    {
        using var r = comando.ExecuteReader();
        var lista = new List<Partido>();
        while (r.Read())
            lista.Add(new Partido(r.GetString(0), r.GetString(1), r.GetString(2), BaseDatos.Texto(r, "superficie"),
                                  r.GetString(4), r.GetString(5), r.GetInt32(6), r.GetString(7), r.GetInt32(8), r.GetString(9)));
        return lista;
    }
}
