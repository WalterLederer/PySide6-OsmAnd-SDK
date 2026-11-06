Set-Location "$env:LOCALAPPDATA\LumaScope\OsmAnd-SDK"

$sdk = "$env:LOCALAPPDATA\LumaScope\OsmAnd-SDK"
$exe = Join-Path $sdk "tools\osmand_render_helper_native\dist\osmand_atlas_render_helper.exe"
$obf = "C:\__Daten\Dev\__LumaScope\source\tools\Austria_wien_europe.obf"
$resources = Join-Path $sdk "vendor\osmand\resources"
$style = Join-Path $resources "rendering_styles\default.render.xml"
$log = Join-Path $sdk "dist\atlas-persistent-test.log"
$stderrLog = Join-Path $sdk "dist\atlas-persistent-test-stderr.log"
$rgba = Join-Path $sdk "dist\atlas-persistent-test.rgba"

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format 'HH:mm:ss.fff') $Message"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Read-ExactTimeout($Stream, [int]$Count, [int]$TimeoutSeconds) {
    $buffer = New-Object byte[] $Count
    $offset = 0
    $timer = [System.Diagnostics.Stopwatch]::StartNew()

    while ($offset -lt $Count) {
        $remaining = ($TimeoutSeconds * 1000) - [int]$timer.ElapsedMilliseconds
        if ($remaining -le 0) {
            throw "Timeout beim Lesen: $offset von $Count Bytes empfangen."
        }

        $task = $Stream.ReadAsync($buffer, $offset, $Count - $offset)

        if (-not $task.Wait($remaining)) {
            throw "Timeout nach $TimeoutSeconds Sekunden: $offset von $Count Bytes empfangen."
        }

        $read = $task.GetAwaiter().GetResult()

        if ($read -le 0) {
            throw "Unerwartetes Ende des Datenstroms: $offset von $Count Bytes gelesen."
        }

        $offset += $read
    }

    return ,$buffer
}

$proc = $null
$stderrTask = $null

try {
    if (Test-Path -LiteralPath $log) {
        Clear-Content -LiteralPath $log
    }

    if (Test-Path -LiteralPath $stderrLog) {
        Clear-Content -LiteralPath $stderrLog
    }

    Write-Log "=== Atlas persistent helper test ==="
    Write-Log "Executable: $exe"
    Write-Log "OBF: $obf"
    Write-Log "Resources: $resources"
    Write-Log "Style: $style"
    Write-Log "stderr file: $stderrLog"

    foreach ($file in @($exe, $obf, $style)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Datei nicht gefunden: $file"
        }
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = "`"$obf`" `"$resources`" `"$style`""
    $psi.WorkingDirectory = $sdk
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    Write-Log "Starting helper..."

    if (-not $proc.Start()) {
        throw "Helper konnte nicht gestartet werden."
    }

    Write-Log "Helper PID: $($proc.Id)"

    $stderrTask = $proc.StandardError.ReadToEndAsync()
    Write-Log "Helper stderr asynchronous reader started."

    $inputStream = $proc.StandardInput
    $outputStream = $proc.StandardOutput.BaseStream

    $request = @{
        type = "render"
        requestId = 1
        width = 1920
        height = 1440
        longitude = 16.3738
        latitude = 48.2082
        zoom = 13
    } | ConvertTo-Json -Compress

    Write-Log "Sending render request..."
    $inputStream.WriteLine($request)
    $inputStream.Flush()

    Write-Log "Waiting for 40-byte frame header (timeout 45 seconds)..."

    [byte[]]$header = Read-ExactTimeout $outputStream 40 45

    $magic = [System.Text.Encoding]::ASCII.GetString($header, 0, 4)
    $version = [BitConverter]::ToUInt16($header, 4)
    $headerSize = [BitConverter]::ToUInt16($header, 6)
    $status = [BitConverter]::ToUInt32($header, 8)
    $requestId = [BitConverter]::ToUInt32($header, 12)
    $width = [BitConverter]::ToUInt32($header, 16)
    $height = [BitConverter]::ToUInt32($header, 20)
    $stride = [BitConverter]::ToUInt32($header, 24)
    $pixelFormat = [BitConverter]::ToUInt32($header, 28)
    $payloadLength = [BitConverter]::ToUInt64($header, 32)

    Write-Log "Header magic: $magic"
    Write-Log "Version: $version"
    Write-Log "Header size: $headerSize"
    Write-Log "Status: $status"
    Write-Log "Request ID: $requestId"
    Write-Log "Resolution: ${width}x${height}"
    Write-Log "Stride: $stride"
    Write-Log "Pixel format: $pixelFormat"
    Write-Log "Payload length: $payloadLength"

    if ($magic -ne "OSMA") {
        throw "Ungueltige Header-Signatur: $magic"
    }

    if ($status -ne 0) {
        Write-Log "Helper returned error status."

        if ($proc.WaitForExit(5000)) {
            $proc.WaitForExit()
            Write-Log "Helper exit code: $($proc.ExitCode)"
        } else {
            Write-Log "WARNING: Helper has not exited after 5 seconds."
        }

        if (Test-Path -LiteralPath $stderrLog) {
            Write-Log "--- HELPER STDERR BEGIN ---"

            Get-Content -LiteralPath $stderrLog | ForEach-Object {
                Write-Log "HELPER: $_"
            }

            Write-Log "--- HELPER STDERR END ---"
        } else {
            Write-Log "WARNING: Helper stderr file is empty or missing."
        }

        throw "Atlas-Helper meldet Fehlerstatus: $status"
    }

    if ($version -ne 1 -or $headerSize -ne 40) {
        throw "Unbekannte Protokollversion oder Headergroesse."
    }

    if ($payloadLength -ne ([uint64]$stride * [uint64]$height)) {
        throw "Payload-Laenge stimmt nicht mit Stride x Hoehe ueberein."
    }

    if ($payloadLength -gt 500000000) {
        throw "Payload unerwartet gross: $payloadLength"
    }

    Write-Log "Reading RGBA payload..."

    [byte[]]$pixels = Read-ExactTimeout $outputStream ([int]$payloadLength) 60

    [System.IO.File]::WriteAllBytes($rgba, $pixels)

    Write-Log "RGBA file written: $rgba"
    Write-Log "Received bytes: $($pixels.Length)"

    $unique = @{}

    for ($i = 0; $i -lt $pixels.Length; $i += 4096) {
        $end = [Math]::Min($i + 3, $pixels.Length - 1)
        $key = "$($pixels[$i]),$($pixels[$i+1]),$($pixels[$i+2]),$($pixels[$end])"
        $unique[$key] = $true
    }

    Write-Log "Unique sampled RGBA values: $($unique.Count)"

    Write-Log "Sending quit..."
    $inputStream.WriteLine('{"type":"quit"}')
    $inputStream.Flush()
    $inputStream.Close()

    Write-Log "Waiting for helper exit..."

    if (-not $proc.WaitForExit(10000)) {
        throw "Helper beendet sich nach quit nicht innerhalb von 10 Sekunden."
    }

    $proc.WaitForExit()

    Write-Log "Exit code: $($proc.ExitCode)"
    Write-Log "=== TEST COMPLETED ==="
    
} catch {
  
    Write-Log "ERROR: $($_.Exception.Message)"
    Write-Log $_.ScriptStackTrace
    
} finally {
  
    if ($null -ne $proc) {
        if (-not $proc.HasExited) {
            Write-Log "Stopping remaining helper process..."

            try { $proc.Kill() } catch {}
            try { $proc.WaitForExit(3000) } catch {}
        }

        try { $proc.WaitForExit() } catch {}

        if ($null -ne $stderrTask) {
            try {
                if ($stderrTask.Wait(5000)) {
                    $stderrText = $stderrTask.GetAwaiter().GetResult()

                    [System.IO.File]::WriteAllText(
                        $stderrLog,
                        $stderrText,
                        (New-Object System.Text.UTF8Encoding($false))
                    )

                    Write-Log "Helper stderr saved: $stderrLog"
                } else {
                    Write-Log "WARNING: stderr reader did not finish."
                }
            } catch {
                Write-Log "WARNING: Could not save stderr: $($_.Exception.Message)"
            }
        }

        if ($proc.HasExited) {
            Write-Log "Final helper exit code: $($proc.ExitCode)"
        }

        $proc.Dispose()
    }

    Write-Host ""
    Write-Host "Hauptprotokoll: $log"
    Write-Host "Helper stderr:   $stderrLog"
    Write-Host "RGBA-Datei:      $rgba"
    Write-Host ""
    Read-Host "Test beendet. Enter zum Schliessen"
}
