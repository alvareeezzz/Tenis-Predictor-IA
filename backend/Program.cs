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

var app = builder.Build();

// Carga el modelo al arrancar: si faltan los datos, falla aquí y no en la primera petición
app.Services.GetRequiredService<PrediccionService>();

if (app.Environment.IsDevelopment())
    app.MapOpenApi();

// En producción el front compilado de Angular vivirá en wwwroot/
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

    var (probA, probEloA, h2h, comparativa) = prediccion.Predecir(peticion);
    return Results.Ok(new PrediccionResponse(
        jugadores.Resumen(peticion.JugadorA)!, jugadores.Resumen(peticion.JugadorB)!,
        Math.Round(probA, 4), Math.Round(1 - probA, 4), Math.Round(probEloA, 4),
        h2h, comparativa, prediccion.FechaDatos.ToString("yyyy-MM-dd")));
});

// Cualquier otra ruta que no sea /api devolverá la app de Angular (fase 7)
app.MapFallbackToFile("index.html");

app.Run();

// Para que el proyecto de tests pueda arrancar la API (WebApplicationFactory<Program>)
public partial class Program;
