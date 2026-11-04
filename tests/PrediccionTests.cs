using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using TenisPredictor.Api.Models;
using TenisPredictor.Api.Services;

namespace TenisPredictor.Tests;

// Necesitan backend/Datos generado por el pipeline de Python (06_exportar.py)
public class PrediccionTests : IClassFixture<WebApplicationFactory<Program>>
{
    private static readonly string DirectorioDatos = Path.Combine(BuscarRaiz(), "backend", "Datos");
    private readonly WebApplicationFactory<Program> _fabrica;

    public PrediccionTests(WebApplicationFactory<Program> fabrica) => _fabrica = fabrica;

    // Lo que calculó Python con el mismo modelo: .NET tiene que dar exactamente lo mismo
    private record CasoPrueba(int JugadorA, int JugadorB, string NombreA, string NombreB,
                              string Superficie, string Nivel, string Ronda, int MejorDe, double ProbA);

    public static IEnumerable<object[]> Casos() =>
        JsonSerializer.Deserialize<List<CasoPrueba>>(
                File.ReadAllText(Path.Combine(DirectorioDatos, "casos_prueba.json")),
                new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower })!
            .Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(Casos))]
    public void DaLaMismaProbabilidadQuePython(object caso)
    {
        var c = (CasoPrueba)caso;
        using var servicio = new PrediccionService(new BaseDatos(DirectorioDatos));
        var (probA, _, _, _) = servicio.Predecir(new PrediccionRequest(c.JugadorA, c.JugadorB, c.Superficie, c.Nivel, c.Ronda, c.MejorDe));
        Assert.Equal(c.ProbA, probA, tolerance: 1e-5);  // Python la guarda redondeada a 6 decimales
    }

    [Fact]
    public void ElOrdenDeLosJugadoresNoCambiaElResultado()
    {
        using var servicio = new PrediccionService(new BaseDatos(DirectorioDatos));
        var (ab, _, _, _) = servicio.Predecir(new PrediccionRequest(206173, 207989, "Clay", "G", "F", 5));
        var (ba, _, _, _) = servicio.Predecir(new PrediccionRequest(207989, 206173, "Clay", "G", "F", 5));
        Assert.Equal(1.0, ab + ba, 6);
    }

    [Fact]
    public async Task EndpointDePrediccionDevuelveProbabilidadesQueSuman1()
    {
        var cliente = _fabrica.CreateClient();
        var respuesta = await cliente.PostAsJsonAsync("/api/prediccion", new PrediccionRequest(206173, 207989, "Grass"));
        respuesta.EnsureSuccessStatusCode();
        var prediccion = await respuesta.Content.ReadFromJsonAsync<PrediccionResponse>();
        Assert.Equal("Jannik Sinner", prediccion!.JugadorA.Nombre);
        Assert.Equal(1.0, prediccion.ProbabilidadA + prediccion.ProbabilidadB, 3);
        Assert.NotEmpty(prediccion.Comparativa);
    }

    [Theory]
    [InlineData(206173, 206173, "Hard")]   // mismo jugador
    [InlineData(206173, 207989, "Carpet")] // superficie que ya no existe
    public async Task EndpointDePrediccionRechazaPeticionesNoValidas(int a, int b, string superficie)
    {
        var cliente = _fabrica.CreateClient();
        var respuesta = await cliente.PostAsJsonAsync("/api/prediccion", new PrediccionRequest(a, b, superficie));
        Assert.Equal(HttpStatusCode.BadRequest, respuesta.StatusCode);
    }

    [Fact]
    public async Task BuscadorEncuentraJugadoresActivos()
    {
        var cliente = _fabrica.CreateClient();
        var jugadores = await cliente.GetFromJsonAsync<List<JugadorResumen>>("/api/jugadores?buscar=alcaraz");
        Assert.Contains(jugadores!, j => j.Nombre == "Carlos Alcaraz");
    }

    [Fact]
    public async Task H2HCuentaLasVictoriasDeCadaUno()
    {
        var cliente = _fabrica.CreateClient();
        var h2h = await cliente.GetFromJsonAsync<H2H>("/api/h2h/206173/207989");
        Assert.Equal(h2h!.Partidos.Count, h2h.Total.VictoriasA + h2h.Total.VictoriasB);
        Assert.True(h2h.Partidos.Count >= 17);
    }

    private static string BuscarRaiz()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "data-pipeline")))
            dir = dir.Parent;
        return dir?.FullName ?? throw new DirectoryNotFoundException("No se encuentra la raíz del repositorio.");
    }
}
