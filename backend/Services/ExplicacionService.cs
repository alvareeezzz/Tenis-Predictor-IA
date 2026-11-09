using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;
using Anthropic;
using Anthropic.Models.Beta;
using Anthropic.Models.Beta.Messages;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Options;
using TenisPredictor.Api.Models;

namespace TenisPredictor.Api.Services;

// Explica un pronóstico en lenguaje natural con Claude, usando solo los datos que enseña la app
public class ExplicacionService(
    AnthropicClient client, IOptions<ExplicacionOptions> opciones, PrediccionService prediccion,
    JugadoresService jugadores, IMemoryCache cache)
{
    private readonly ExplicacionOptions _opciones = opciones.Value;

    private const string Instrucciones = """
        Eres un analista de tenis que explica, en español, el pronóstico de un modelo estadístico: un XGBoost
        entrenado con más de 300.000 partidos ATP que usa el Elo de cada jugador (general y por superficie),
        sus estadísticas de saque y resto, su forma, su descanso y los enfrentamientos directos.

        Recibirás los datos de un partido y las probabilidades del modelo. Escribe un párrafo de 3 a 5 frases,
        de 110 palabras como máximo, en texto plano, sin markdown ni listas:
        - Empieza por quién es favorito y con qué probabilidad.
        - Explica las dos o tres diferencias de los datos que más lo justifican, con sus cifras.
        - Si la probabilidad del modelo y la del Elo se separan más de 8 puntos, o algún dato pide cautela
          (muchos días sin jugar, pocos partidos recientes, enfrentamientos de hace años), dilo.

        Usa solo los datos recibidos: no añadas lesiones, resultados ni hechos que no estén en ellos.
        No des consejos de apuesta. Nombra a los jugadores por su apellido.
        """;

    // Sin clave no hay explicación: la API responde 503 y el resto de la app funciona igual
    public bool Configurada => !string.IsNullOrWhiteSpace(client.ApiKey);

    public async IAsyncEnumerable<string> Explicar(PrediccionRequest p, [EnumeratorCancellation] CancellationToken ct)
    {
        // El mismo partido con los mismos datos da la misma explicación: no se paga dos veces
        var clave = $"explicacion|{prediccion.FechaDatos}|{p}";
        if (cache.TryGetValue(clave, out string? guardada) && guardada is not null)
        {
            yield return guardada;
            yield break;
        }

        var parametros = new MessageCreateParams
        {
            Model = _opciones.Modelo,
            MaxTokens = _opciones.MaxTokens,
            OutputConfig = new BetaOutputConfig { Effort = Effort.Low }, // texto corto: más rápido y barato
            System = Instrucciones,
            Messages = [new BetaUserMessageParam { Content = Contexto(p) }],
            // Si el modelo rechaza la petición por seguridad, la API la reintenta con otro modelo
            Betas = [AnthropicBeta.ServerSideFallback2026_07_01],
            Fallbacks = new Default(),
        };

        var texto = new StringBuilder();
        var rechazada = false;
        await foreach (var evento in client.Beta.Messages.CreateStreaming(parametros, ct))
        {
            if (evento.TryPickContentBlockDelta(out var bloque) && bloque.Delta.TryPickText(out var trozo))
            {
                texto.Append(trozo.Text);
                yield return trozo.Text;
            }
            else if (evento.TryPickDelta(out var final) && final.Delta.StopReason == BetaStopReason.Refusal)
            {
                rechazada = true;
            }
        }

        if (rechazada && texto.Length == 0)
            yield return "No he podido escribir una explicación para este partido.";
        else if (texto.Length > 0)
            cache.Set(clave, texto.ToString(), TimeSpan.FromHours(_opciones.HorasCache));
    }

    // Los datos del partido, en el mismo orden en que se leen en la página
    public string Contexto(PrediccionRequest p)
    {
        var (probA, probEloA, h2h, comparativa) = prediccion.Predecir(p);
        var a = jugadores.Resumen(p.JugadorA)!;
        var b = jugadores.Resumen(p.JugadorB)!;
        var estadoA = prediccion.Estado(p.JugadorA, p.Superficie);
        var estadoB = prediccion.Estado(p.JugadorB, p.Superficie);
        var enfrentamientos = jugadores.EnfrentamientoDirecto(p.JugadorA, p.JugadorB);
        var es = CultureInfo.GetCultureInfo("es-ES");

        var s = new StringBuilder();
        s.AppendLine($"Partido: {a.Nombre} contra {b.Nombre}");
        s.AppendLine($"Superficie: {Superficie(p.Superficie)}. Torneo: {Nivel(p.Nivel)}. Ronda: {Ronda(p.Ronda)}. Al mejor de {p.MejorDe} sets.");
        s.AppendLine($"Datos hasta el {prediccion.FechaDatos:d 'de' MMMM 'de' yyyy}. El ranking ATP es el del 8 de junio de 2026 (no hay uno más reciente).");
        s.AppendLine();
        s.AppendLine($"Probabilidad del modelo: {a.Nombre} {probA.ToString("P0", es)}, {b.Nombre} {(1 - probA).ToString("P0", es)}");
        s.AppendLine($"Probabilidad solo con el Elo: {a.Nombre} {probEloA.ToString("P0", es)}");
        s.AppendLine();
        s.AppendLine($"Enfrentamientos directos: {a.Nombre} {h2h.VictoriasA} - {h2h.VictoriasB} {b.Nombre}");
        if (enfrentamientos is { Partidos.Count: > 0 })
        {
            foreach (var (superficie, balance) in enfrentamientos.PorSuperficie)
                s.AppendLine($"  en {Superficie(superficie)}: {balance.VictoriasA} - {balance.VictoriasB}");
            var ultimo = enfrentamientos.Partidos[0];
            s.AppendLine($"  último: {ultimo.Fecha}, {ultimo.Torneo}, ganó {ultimo.GanadorNombre} ({ultimo.Marcador})");
        }
        s.AppendLine();
        s.AppendLine($"Estadística | {a.Nombre} | {b.Nombre}");
        foreach (var c in comparativa)
            s.AppendLine($"{c.Nombre} | {Valor(c.Clave, c.ValorA, es)} | {Valor(c.Clave, c.ValorB, es)}");
        foreach (var (claveEstado, nombre) in new[]
                 {
                     ("edad", "Edad"), ("dias_desde_ultimo_torneo", "Días desde su último torneo"),
                     ("partidos_14d", "Partidos en los últimos 14 días"), ("partidos_52s", "Partidos en las últimas 52 semanas"),
                 })
            s.AppendLine($"{nombre} | {Valor(claveEstado, estadoA[claveEstado], es)} | {Valor(claveEstado, estadoB[claveEstado], es)}");
        return s.ToString();
    }

    private static string Valor(string clave, double? valor, CultureInfo es) => valor switch
    {
        null or double.NaN => "sin datos",
        { } v when clave.StartsWith("pct_") => v.ToString("P1", es),
        { } v when clave.StartsWith("victorias_ult") => $"{Math.Round(v * 10)} de 10",
        { } v when clave is "aces_partido_52s" or "dominance_ratio_52s" => v.ToString("0.##", es),
        { } v when clave is "edad" => $"{Math.Floor(v)} años",
        { } v => v.ToString("0", es),
    };

    private static string Superficie(string s) => s switch
    {
        "Hard" => "pista dura", "Clay" => "tierra batida", "Grass" => "hierba", _ => s
    };

    private static string Ronda(string r) => r switch
    {
        "R128" => "primera ronda", "R64" => "segunda ronda", "R32" => "dieciseisavos", "R16" => "octavos",
        "QF" => "cuartos de final", "SF" => "semifinal", "F" => "final", "RR" => "fase de grupos", _ => r
    };

    private static string Nivel(string n) => n switch
    {
        "G" => "Grand Slam", "M" => "Masters 1000", "A" => "ATP 250/500", "F" => "ATP Finals",
        "D" => "Copa Davis", "O" => "Juegos Olímpicos", "C" => "Challenger", _ => n
    };
}
