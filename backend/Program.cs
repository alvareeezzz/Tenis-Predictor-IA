using System.Threading.RateLimiting;
using Anthropic;
using Anthropic.Exceptions;
using Microsoft.Extensions.Options;
using TenisPredictor.Api.Models;
using TenisPredictor.Api.Services;

var builder = WebApplication.CreateBuilder(args);

// tenis.db, modelo.onnx y modelo.json los genera data-pipeline/06_exportar.py en Datos/
builder.Services.Configure<DatosOptions>(builder.Configuration.GetSection("Datos"));
builder.Services.AddSingleton(sp => new BaseDatos(Path.Combine(
    sp.GetRequiredService<IWebHostEnvironment>().ContentRootPath,
    sp.GetRequiredService<IOptions<DatosOptions>>().Value.Directorio)));
builder.Services.AddSingleton<JugadoresService>();
builder.Services.AddSingleton<PrediccionService>();
builder.Services.AddOpenApi();

// Explicación con Claude. La clave se lee de user-secrets (desarrollo) o de la variable
// ANTHROPIC_API_KEY (producción); nunca va en appsettings.json ni llega al navegador.
builder.Services.Configure<ExplicacionOptions>(builder.Configuration.GetSection("Explicacion"));
builder.Services.AddSingleton(_ => new AnthropicClient
{
    ApiKey = builder.Configuration["Anthropic:ApiKey"] is { Length: > 0 } clave ? clave : Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY")
});
builder.Services.AddMemoryCache();
builder.Services.AddSingleton<ExplicacionService>();

// Cada explicación cuesta una llamada a la API: límite por visitante
var explicacionesPorMinuto = builder.Configuration.GetValue("Explicacion:PeticionesPorMinuto", 10);
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    options.AddPolicy("explicacion", context => RateLimitPartition.GetFixedWindowLimiter(
        context.Connection.RemoteIpAddress?.ToString() ?? "desconocida",
        _ => new FixedWindowRateLimiterOptions { PermitLimit = explicacionesPorMinuto, Window = TimeSpan.FromMinutes(1) }));
    options.OnRejected = async (context, token) =>
        await context.HttpContext.Response.WriteAsJsonAsync(
            new ErrorApi("Demasiadas explicaciones seguidas. Espera un minuto."), token);
});

var app = builder.Build();

// Carga el modelo al arrancar: si faltan los datos, falla aquí y no en la primera petición
app.Services.GetRequiredService<PrediccionService>();

if (app.Environment.IsDevelopment())
    app.MapOpenApi();

app.UseRateLimiter();

// En producción el front compilado de Angular vive en wwwroot/
app.UseDefaultFiles();
app.UseStaticFiles();

var api = app.MapGroup("/api");

api.MapGet("/info", (BaseDatos bd) =>
{
    using var conexion = bd.Abrir();
    using var comando = BaseDatos.Comando(conexion, "SELECT clave, valor FROM metadatos");
    using var r = comando.ExecuteReader();
    var info = new Dictionary<string, string>();
    while (r.Read()) info[r.GetString(0)] = r.GetString(1);
    return info;
});

api.MapGet("/jugadores", (string? buscar, int? limite, JugadoresService jugadores) =>
    string.IsNullOrWhiteSpace(buscar) || buscar.Trim().Length < 2
        ? Results.BadRequest(new ErrorApi("Escribe al menos 2 letras del nombre."))
        : Results.Ok(jugadores.Buscar(buscar, Math.Clamp(limite ?? 10, 1, 50))));

api.MapGet("/jugadores/top", (string? superficie, int? limite, JugadoresService jugadores) =>
{
    superficie ??= "General";
    return JugadoresService.EsSuperficieValida(superficie)
        ? Results.Ok(jugadores.MejoresPorElo(superficie, Math.Clamp(limite ?? 20, 1, 100)))
        : Results.BadRequest(new ErrorApi("Superficie no válida: usa General, Hard, Clay o Grass."));
});

api.MapGet("/jugadores/{id:int}", (int id, JugadoresService jugadores) =>
    jugadores.Ficha(id) is { } ficha ? Results.Ok(ficha) : Results.NotFound(new ErrorApi("Jugador no encontrado.")));

api.MapGet("/jugadores/{id:int}/elo", (int id, JugadoresService jugadores) =>
    jugadores.HistorialElo(id) is { Count: > 0 } historial ? Results.Ok(historial) : Results.NotFound(new ErrorApi("Jugador no encontrado.")));

api.MapGet("/h2h/{a:int}/{b:int}", (int a, int b, JugadoresService jugadores) =>
    jugadores.EnfrentamientoDirecto(a, b) is { } h2h ? Results.Ok(h2h) : Results.NotFound(new ErrorApi("Jugador no encontrado.")));

api.MapPost("/prediccion", (PrediccionRequest peticion, PrediccionService prediccion, JugadoresService jugadores) =>
{
    if (Validar(peticion, prediccion) is { } error)
        return error;

    var (probA, probEloA, h2h, comparativa) = prediccion.Predecir(peticion);
    return Results.Ok(new PrediccionResponse(
        jugadores.Resumen(peticion.JugadorA)!, jugadores.Resumen(peticion.JugadorB)!,
        Math.Round(probA, 4), Math.Round(1 - probA, 4), Math.Round(probEloA, 4),
        h2h, comparativa, prediccion.FechaDatos.ToString("yyyy-MM-dd")));
});

// El texto llega a trozos (text/plain) según lo escribe Claude
api.MapPost("/explicacion", async (PrediccionRequest peticion, PrediccionService prediccion,
    ExplicacionService explicacion, ILogger<Program> logger, CancellationToken ct) =>
{
    if (Validar(peticion, prediccion) is { } error)
        return error;
    if (!explicacion.Configurada)
        return Results.Json(new ErrorApi("La explicación con IA no está configurada en este servidor."), statusCode: 503);

    // Se lee el primer trozo antes de responder: así los errores de la llamada llevan su código HTTP
    var trozos = explicacion.Explicar(peticion, ct).GetAsyncEnumerator(ct);
    try
    {
        if (await trozos.MoveNextAsync())
            return new TextoEnStreaming(trozos.Current, trozos, logger);
        await trozos.DisposeAsync();
        return Results.Json(new ErrorApi("Claude no ha devuelto ninguna explicación."), statusCode: 502);
    }
    catch (AnthropicRateLimitException)
    {
        await trozos.DisposeAsync();
        return Results.Json(new ErrorApi("Mucha gente pidiendo explicaciones a la vez. Prueba en un momento."), statusCode: 503);
    }
    catch (AnthropicUnauthorizedException)
    {
        await trozos.DisposeAsync();
        logger.LogError("Clave de API no válida. Revisa Anthropic:ApiKey en user-secrets o ANTHROPIC_API_KEY.");
        return Results.Json(new ErrorApi("La explicación con IA no está disponible ahora mismo."), statusCode: 502);
    }
    catch (Exception ex) when (ex is not OperationCanceledException)
    {
        await trozos.DisposeAsync();
        logger.LogError(ex, "Error llamando a Claude");
        return Results.Json(new ErrorApi("La explicación con IA no está disponible ahora mismo."), statusCode: 502);
    }
}).RequireRateLimiting("explicacion");

// Cualquier otra ruta que no sea /api devuelve la app de Angular
app.MapFallbackToFile("index.html");

app.Run();

// Lo que comparten /prediccion y /explicacion: un partido que el modelo sepa pronosticar
static IResult? Validar(PrediccionRequest peticion, PrediccionService prediccion)
{
    if (peticion.JugadorA == peticion.JugadorB)
        return Results.BadRequest(new ErrorApi("Elige dos jugadores distintos."));
    if (!PrediccionService.Superficies.Contains(peticion.Superficie))
        return Results.BadRequest(new ErrorApi("Superficie no válida: usa Hard, Clay o Grass."));
    if (peticion.Nivel is not ("G" or "M" or "A" or "F" or "D" or "O" or "C"))
        return Results.BadRequest(new ErrorApi("Nivel no válido: usa G, M, A, F, D, O o C."));
    if (!prediccion.EsRondaValida(peticion.Ronda))
        return Results.BadRequest(new ErrorApi("Ronda no válida."));
    if (peticion.MejorDe is not (3 or 5))
        return Results.BadRequest(new ErrorApi("Los partidos son al mejor de 3 o de 5 sets."));
    if (!prediccion.PuedePronosticar(peticion.JugadorA) || !prediccion.PuedePronosticar(peticion.JugadorB))
        return Results.NotFound(new ErrorApi("Solo se pueden pronosticar jugadores con partidos en los dos últimos años."));
    return null;
}

// Para que el proyecto de tests pueda arrancar la API (WebApplicationFactory<Program>)
public partial class Program;
