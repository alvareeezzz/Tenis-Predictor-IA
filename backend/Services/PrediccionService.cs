using System.Text.Json;
using Microsoft.ML.OnnxRuntime;
using Microsoft.ML.OnnxRuntime.Tensors;
using TenisPredictor.Api.Models;

namespace TenisPredictor.Api.Services;

// Lo que describe modelo.json (generado por data-pipeline/06_exportar.py)
public record ModeloContrato(
    List<string> Columnas, List<string> VariablesJugador, List<string> H2hComunes,
    Dictionary<string, int> OrdenRondas, int DiasH2hReciente, double PesoSuperficie);

// Reconstruye las variables del modelo igual que matriz() en data-pipeline/05_entrenar.py
// y ejecuta el modelo ONNX. Los tests comparan el resultado con backend/Datos/casos_prueba.json.
public sealed class PrediccionService : IDisposable
{
    public static readonly string[] Superficies = ["Hard", "Clay", "Grass"];

    private static readonly Dictionary<string, string> ColumnaNivel = new()
    {
        ["nivel_grand_slam"] = "G",
        ["nivel_masters"] = "M",
        ["nivel_finals"] = "F",
        ["nivel_copa_davis"] = "D",
        ["nivel_challenger"] = "C"
    };

    // Estadísticas que se enseñan al usuario junto al pronóstico
    private static readonly (string Clave, string Nombre, bool MayorEsMejor)[] ClavesComparativa =
    [
        ("elo_comb", "Elo en esta superficie (combinado)", true),
        ("log_rank", "Ranking ATP", false),
        ("dominance_ratio_52s", "Dominance ratio (52 semanas)", true),
        ("pct_puntos_totales_sup", "% puntos ganados en la superficie", true),
        ("pct_puntos_saque_sup", "% puntos ganados al saque en la superficie", true),
        ("pct_puntos_resto_sup", "% puntos ganados al resto en la superficie", true),
        ("pct_juegos_saque_52s", "% juegos de saque ganados (52 semanas)", true),
        ("aces_partido_52s", "Aces por partido (52 semanas)", true),
        ("pct_bp_salvados_52s", "% break points salvados (52 semanas)", true),
        ("pct_tiebreaks_52s", "% tie-breaks ganados (52 semanas)", true),
        ("victorias_ult10", "Victorias en los últimos 10 partidos", true),
    ];

    private readonly BaseDatos _bd;
    private readonly InferenceSession _sesion;
    private readonly Dictionary<(int Jugador, string Superficie), Dictionary<string, double>> _estado = [];

    public ModeloContrato Modelo { get; }
    public DateOnly FechaDatos { get; }

    public PrediccionService(BaseDatos bd)
    {
        _bd = bd;
        var opcionesJson = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        Modelo = JsonSerializer.Deserialize<ModeloContrato>(File.ReadAllText(bd.Ruta("modelo.json")), opcionesJson)!;
        _sesion = new InferenceSession(bd.Ruta("modelo.onnx"));

        using var conexion = bd.Abrir();
        using (var comando = BaseDatos.Comando(conexion, "SELECT valor FROM metadatos WHERE clave = 'fecha_datos'"))
            FechaDatos = DateOnly.Parse((string)comando.ExecuteScalar()!);

        // El estado actual de cada jugador (unos 2.000 × 3 superficies) cabe de sobra en memoria
        using (var comando = BaseDatos.Comando(conexion, "SELECT * FROM estado_jugador"))
        using (var r = comando.ExecuteReader())
        {
            while (r.Read())
            {
                var valores = new Dictionary<string, double>();
                for (var i = 2; i < r.FieldCount; i++)
                    valores[r.GetName(i)] = BaseDatos.Numero(r, r.GetName(i)) ?? double.NaN;
                _estado[(r.GetInt32(0), r.GetString(1))] = valores;
            }
        }
    }

    public bool PuedePronosticar(int jugador) => _estado.ContainsKey((jugador, "Hard"));

    public bool EsRondaValida(string ronda) => Modelo.OrdenRondas.ContainsKey(ronda);

    public (double ProbabilidadA, double ProbabilidadEloA, BalanceH2H H2H, List<Comparativa> Comparativa) Predecir(PrediccionRequest p)
    {
        var a = Lado(p.JugadorA, p.JugadorB, p.Superficie, p.VieneDePreviaA);
        var b = Lado(p.JugadorB, p.JugadorA, p.Superficie, p.VieneDePreviaB);

        // Se predice el partido en los dos órdenes y se promedia, como en predecir() de 05_entrenar.py:
        // así P(A gana) + P(B gana) = 1 exactamente
        var n = Modelo.Columnas.Count;
        var datos = new float[2 * n];
        Variables(a, b, p, datos.AsSpan(0, n));
        Variables(b, a, p, datos.AsSpan(n, n));
        var entrada = new DenseTensor<float>(datos, [2, n]);
        using var resultados = _sesion.Run([NamedOnnxValue.CreateFromTensor("variables", entrada)]);
        var probabilidades = resultados.First(x => x.Name == "probabilities").AsTensor<float>();
        var probA = (probabilidades[0, 1] + 1 - probabilidades[1, 1]) / 2.0;

        var probEloA = 1 / (1 + Math.Pow(10, (b["elo_comb"] - a["elo_comb"]) / 400));
        var h2h = new BalanceH2H((int)a["h2h_victorias"], (int)b["h2h_victorias"]);
        var comparativa = ClavesComparativa.Select(c => new Comparativa(
            c.Clave, c.Nombre, ParaMostrar(c.Clave, a), ParaMostrar(c.Clave, b), c.MayorEsMejor)).ToList();
        return (probA, probEloA, h2h, comparativa);
    }

    // Valores de un jugador para este partido: su estado actual en la superficie + H2H contra el rival
    private Dictionary<string, double> Lado(int jugador, int rival, string superficie, bool vieneDePrevia)
    {
        var valores = new Dictionary<string, double>(_estado[(jugador, superficie)])
        {
            ["viene_de_previa"] = vieneDePrevia ? 1 : 0
        };
        foreach (var (clave, valor) in EnfrentamientoDirecto(jugador, rival, superficie))
            valores[clave] = valor;
        return valores;
    }

    private Dictionary<string, double> EnfrentamientoDirecto(int jugador, int rival, string superficie)
    {
        using var conexion = _bd.Abrir();
        using var comando = BaseDatos.Comando(conexion, """
            SELECT ganador_id, sets_ganador, sets_perdedor, superficie_elo, fecha FROM partidos
            WHERE (ganador_id = $a AND perdedor_id = $b) OR (ganador_id = $b AND perdedor_id = $a)
            """, ("$a", jugador), ("$b", rival));
        using var r = comando.ExecuteReader();
        var desdeReciente = FechaDatos.AddDays(-Modelo.DiasH2hReciente);
        double partidos = 0, victorias = 0, partidosSup = 0, victoriasSup = 0, partidos3a = 0, victorias3a = 0, setsA = 0, setsB = 0;
        while (r.Read())
        {
            var gana = r.GetInt32(0) == jugador;
            var (setsGanador, setsPerdedor) = (r.GetDouble(1), r.GetDouble(2));
            setsA += gana ? setsGanador : setsPerdedor;
            setsB += gana ? setsPerdedor : setsGanador;
            var enSuperficie = r.GetString(3) == superficie;
            var reciente = DateOnly.Parse(r.GetString(4)) > desdeReciente;
            partidos++;
            partidosSup += enSuperficie ? 1 : 0;
            partidos3a += reciente ? 1 : 0;
            victorias += gana ? 1 : 0;
            victoriasSup += gana && enSuperficie ? 1 : 0;
            victorias3a += gana && reciente ? 1 : 0;
        }
        return new()
        {
            ["h2h_partidos"] = partidos, ["h2h_victorias"] = victorias,
            ["h2h_sup_partidos"] = partidosSup, ["h2h_sup_victorias"] = victoriasSup,
            ["h2h_3a_partidos"] = partidos3a, ["h2h_3a_victorias"] = victorias3a,
            ["h2h_pct_sets"] = setsA + setsB > 0 ? setsA / (setsA + setsB) : double.NaN
        };
    }

    private void Variables(Dictionary<string, double> x, Dictionary<string, double> y, PrediccionRequest p, Span<float> fila)
    {
        for (var i = 0; i < Modelo.Columnas.Count; i++)
        {
            var columna = Modelo.Columnas[i];
            double valor = columna switch
            {
                _ when columna.StartsWith("dif_") => x[columna[4..]] - y[columna[4..]],  // NaN si falta alguno
                _ when Modelo.H2hComunes.Contains(columna) => x[columna],
                "elo_medio_partido" => (x["elo_comb"] + y["elo_comb"]) / 2,
                "mejor_de_5" => p.MejorDe == 5 ? 1 : 0,
                "ronda" => Modelo.OrdenRondas.GetValueOrDefault(p.Ronda, 0),
                "circuito_principal" => p.Nivel != "C" ? 1 : 0,
                _ when columna.StartsWith("superficie_") => columna == $"superficie_{p.Superficie.ToLowerInvariant()}" ? 1 : 0,
                _ when ColumnaNivel.TryGetValue(columna, out var nivel) => p.Nivel == nivel ? 1 : 0,
                _ => throw new InvalidOperationException($"Variable del modelo desconocida: {columna}")
            };
            fila[i] = (float)valor;
        }
    }

    // El ranking se guarda como -log(ranking) para el modelo: aquí se deshace para enseñarlo.
    // Sin ranking el pipeline usa el puesto 2000, que no se enseña.
    private static double? ParaMostrar(string clave, Dictionary<string, double> valores)
    {
        var v = valores[clave];
        if (double.IsNaN(v)) return null;
        if (clave != "log_rank") return Math.Round(v, 4);
        var ranking = Math.Round(Math.Exp(-v));
        return ranking >= 2000 ? null : ranking;
    }

    public void Dispose() => _sesion.Dispose();
}
