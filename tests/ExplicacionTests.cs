using System.Net;
using System.Net.Http.Json;
using Anthropic;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Options;
using TenisPredictor.Api.Models;
using TenisPredictor.Api.Services;

namespace TenisPredictor.Tests;

// No llaman a Claude: comprueban lo que se le enviaría y lo que pasa cuando no hay clave
public class ExplicacionTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _sinClave;

    public ExplicacionTests(WebApplicationFactory<Program> fabrica) =>
        _sinClave = fabrica.WithWebHostBuilder(b => b.UseSetting("Anthropic:ApiKey", ""));

    [Fact]
    public void ElContextoLlevaLosDatosDelPronostico()
    {
        var bd = new BaseDatos(DirectorioDatos());
        using var prediccion = new PrediccionService(bd);
        var jugadores = new JugadoresService(bd);
        var servicio = new ExplicacionService(new AnthropicClient { ApiKey = null }, Options.Create(new ExplicacionOptions()),
                                              prediccion, jugadores, new MemoryCache(new MemoryCacheOptions()));

        var contexto = servicio.Contexto(new PrediccionRequest(206173, 207989, "Clay", "G", "F", 5));

        Assert.Contains("Jannik Sinner contra Carlos Alcaraz", contexto);
        Assert.Contains("tierra batida", contexto);
        Assert.Contains("Al mejor de 5 sets", contexto);
        Assert.Contains("Probabilidad del modelo:", contexto);
        Assert.Contains("Enfrentamientos directos:", contexto);
        Assert.Contains("Días desde su último torneo", contexto);
        Assert.DoesNotContain("NaN", contexto);
        Assert.False(servicio.Configurada);
    }

    [Fact]
    public async Task SinClaveResponde503ConUnMensaje()
    {
        var respuesta = await _sinClave.CreateClient().PostAsJsonAsync("/api/explicacion", new PrediccionRequest(206173, 207989, "Hard"));
        Assert.Equal(HttpStatusCode.ServiceUnavailable, respuesta.StatusCode);
        var error = await respuesta.Content.ReadFromJsonAsync<ErrorApi>();
        Assert.Contains("no está configurada", error!.Error);
    }

    [Fact]
    public async Task ValidaLaPeticionComoElPronostico()
    {
        var respuesta = await _sinClave.CreateClient().PostAsJsonAsync("/api/explicacion", new PrediccionRequest(206173, 206173, "Hard"));
        Assert.Equal(HttpStatusCode.BadRequest, respuesta.StatusCode);
    }

    private static string DirectorioDatos()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "data-pipeline")))
            dir = dir.Parent;
        return Path.Combine(dir!.FullName, "backend", "Datos");
    }
}
