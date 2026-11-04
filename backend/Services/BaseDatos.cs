using Microsoft.Data.Sqlite;

namespace TenisPredictor.Api.Services;

// Acceso a la carpeta de datos que genera data-pipeline/06_exportar.py
public class BaseDatos(string directorio)
{
    public string Directorio { get; } = directorio;

    public string Ruta(string archivo) => Path.Combine(Directorio, archivo);

    // Solo lectura: la base de datos se regenera entera con el pipeline de Python
    public SqliteConnection Abrir()
    {
        var conexion = new SqliteConnection($"Data Source={Ruta("tenis.db")};Mode=ReadOnly");
        conexion.Open();
        return conexion;
    }

    public static SqliteCommand Comando(SqliteConnection conexion, string sql, params (string Nombre, object? Valor)[] parametros)
    {
        var comando = conexion.CreateCommand();
        comando.CommandText = sql;
        foreach (var (nombre, valor) in parametros)
            comando.Parameters.AddWithValue(nombre, valor ?? DBNull.Value);
        return comando;
    }

    // pandas guarda los enteros con huecos como REAL y los vacíos como NULL (o NaN)
    public static double? Numero(SqliteDataReader r, string columna)
    {
        var i = r.GetOrdinal(columna);
        if (r.IsDBNull(i)) return null;
        var valor = r.GetDouble(i);
        return double.IsNaN(valor) ? null : valor;
    }

    public static int? Entero(SqliteDataReader r, string columna) => Numero(r, columna) is { } v ? (int)v : null;

    public static string? Texto(SqliteDataReader r, string columna)
    {
        var i = r.GetOrdinal(columna);
        return r.IsDBNull(i) ? null : r.GetString(i);
    }
}
