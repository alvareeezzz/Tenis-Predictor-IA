namespace TenisPredictor.Api.Services;

// Respuesta de texto que se envía a trozos según llega de Claude, para que el navegador la vaya enseñando.
// El primer trozo ya está leído: los errores de la llamada (clave, límite...) se han tratado antes de responder.
public sealed class TextoEnStreaming(string primero, IAsyncEnumerator<string> resto, ILogger logger) : IResult
{
    public async Task ExecuteAsync(HttpContext http)
    {
        http.Response.ContentType = "text/plain; charset=utf-8";
        http.Response.Headers.CacheControl = "no-cache";
        await using (resto)
        {
            await http.Response.WriteAsync(primero, http.RequestAborted);
            await http.Response.Body.FlushAsync(http.RequestAborted);
            try
            {
                while (await resto.MoveNextAsync())
                {
                    await http.Response.WriteAsync(resto.Current, http.RequestAborted);
                    await http.Response.Body.FlushAsync(http.RequestAborted);
                }
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                logger.LogError(ex, "La explicación se cortó a medias");
                await http.Response.WriteAsync("\n\n(La explicación se ha cortado. Vuelve a intentarlo.)", http.RequestAborted);
            }
        }
    }
}
