# Actualización semanal desatendida (tarea "Tenis Predictor - actualización semanal" del
# Programador de tareas de Windows, lunes a las 8:30 o en cuanto se encienda el ordenador).
#
#   1. Si la API está arrancada en el puerto 5014, la para: tiene abierta tenis.db.
#   2. Ejecuta actualizar.py.
#   3. Si la API estaba arrancada, la vuelve a arrancar (aunque la actualización falle: en ese
#      caso actualizar.py no ha tocado los datos y la API sigue con los de antes).
#
# Todo queda en data-pipeline/data/actualizacion_programada.log.

$ErrorActionPreference = 'Stop'
$puerto = 5014
$pipeline = $PSScriptRoot
$raiz = Split-Path $pipeline -Parent
$backend = Join-Path $raiz 'backend'
$registro = Join-Path $pipeline 'data\actualizacion_programada.log'

function Anotar([string]$texto) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $texto" | Out-File -FilePath $registro -Append -Encoding utf8
}

Anotar '=== Inicio de la actualización semanal ==='

# 1. Parar la API. Solo se para si el proceso que escucha en el puerto es la API del proyecto.
$apiArrancada = $false
$conexion = Get-NetTCPConnection -LocalPort $puerto -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conexion) {
    $proceso = Get-Process -Id $conexion.OwningProcess -ErrorAction SilentlyContinue
    if ($proceso -and $proceso.ProcessName -in @('TenisPredictor.Api', 'dotnet')) {
        $apiArrancada = $true
        Anotar "Parando la API ($($proceso.ProcessName), PID $($proceso.Id))"
        Stop-Process -Id $proceso.Id -Force
        for ($i = 0; $i -lt 30 -and (Get-NetTCPConnection -LocalPort $puerto -State Listen -ErrorAction SilentlyContinue); $i++) {
            Start-Sleep -Seconds 1
        }
    } else {
        Anotar "El puerto $puerto lo usa otro programa ($($proceso.ProcessName)): no se toca y no se actualiza."
        exit 1
    }
} else {
    Anotar 'La API no estaba arrancada.'
}

# 2. Actualizar los datos
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.Encoding]::UTF8  # para leer bien las tildes que escribe Python
$python = Join-Path $raiz '.venv\Scripts\python.exe'
Anotar 'Ejecutando actualizar.py'
# Los avisos de Python salen por stderr: en PowerShell 5.1 serían errores que cortarían el script
$ErrorActionPreference = 'Continue'
& $python (Join-Path $pipeline 'actualizar.py') *>&1 | ForEach-Object { "    $_" } | Out-File -FilePath $registro -Append -Encoding utf8
$codigo = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($codigo -eq 0) { Anotar 'Actualización terminada.' } else { Anotar "actualizar.py ha fallado (código $codigo): los datos no se han tocado." }

# 3. Volver a arrancar la API, en segundo plano y sin ventana, con su salida en su propio registro
if ($apiArrancada) {
    $salidaApi = Join-Path $pipeline 'data\api.log'
    Start-Process -FilePath 'dotnet' -ArgumentList 'run' -WorkingDirectory $backend -WindowStyle Hidden `
        -RedirectStandardOutput $salidaApi -RedirectStandardError "$salidaApi.err"
    $lista = $false
    for ($i = 0; $i -lt 90 -and -not $lista; $i++) {
        Start-Sleep -Seconds 2
        try { Invoke-WebRequest -UseBasicParsing "http://localhost:$puerto/api/info" -TimeoutSec 2 | Out-Null; $lista = $true } catch { }
    }
    if ($lista) { Anotar "API arrancada de nuevo en http://localhost:$puerto" } else { Anotar "La API no responde tras 3 minutos: revisa $salidaApi" }
}

Anotar '=== Fin ==='
exit $codigo
