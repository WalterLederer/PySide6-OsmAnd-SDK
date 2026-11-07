Set-Location "C:\__Daten\Dev\__LumaScope\OsmAnd-SDK"
$sdk = "C:\__Daten\Dev\__LumaScope\OsmAnd-SDK"

$exe = "C:\__Daten\Dev\__LumaScope\LumaScope\dist\executables\win-unpacked\resources\osmand\helpers\osmand_atlas_render_helper.exe"
$obf = "C:\__Daten\Dev\__LumaScope\LumaScope\tools\Austria_wien_europe.obf"

# $resources = "C:\__Daten\Dev\__LumaScope\LumaScope\dist\executables\win-unpacked\resources\osmand\resources"
# $resources = "C:\__Daten\Dev\__LumaScope\OsmAnd-SDK\vendor\osmand\resources"
$resources = "C:\__Daten\Dev\__LumaScope\LumaScope\tools\osmand-runtime\stage\resources"

$style = "C:\__Daten\Dev\__LumaScope\LumaScope\dist\executables\win-unpacked\resources\osmand\resources\rendering_styles\default.render.xml"
$log = Join-Path $sdk "dist\atlas-persistent-test.log"
$stderrLog = Join-Path $sdk "dist\atlas-persistent-test-stderr.log"
$rgba = Join-Path $sdk "dist\atlas-persistent-test.rgba"
$png = Join-Path $sdk "dist\atlas-persistent-test.png"

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

Add-Type -AssemblyName System.Drawing

$pngWriterSource = @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class AtlasPngWriter
{
    public static void Save(string path, byte[] rgba, int width, int height, int sourceStride)
    {
        if (rgba == null) throw new ArgumentNullException("rgba");
        if (width <= 0 || height <= 0 || sourceStride < width * 4)
            throw new ArgumentOutOfRangeException("Invalid image dimensions or stride.");
        if ((long)sourceStride * height > rgba.LongLength)
            throw new ArgumentException("RGBA buffer is shorter than stride x height.");

        using (var bitmap = new Bitmap(width, height, PixelFormat.Format32bppArgb))
        {
            var rectangle = new Rectangle(0, 0, width, height);
            var data = bitmap.LockBits(rectangle, ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);

            try
            {
                var row = new byte[width * 4];

                for (int y = 0; y < height; y++)
                {
                    Buffer.BlockCopy(rgba, y * sourceStride, row, 0, row.Length);

                    // QImage Format_RGBA8888 is RGBA; Format32bppArgb is BGRA in memory on Windows.
                    for (int x = 0; x < width; x++)
                    {
                        int p = x * 4;
                        byte red = row[p];
                        row[p] = row[p + 2];
                        row[p + 2] = red;
                    }

                    Marshal.Copy(row, 0, IntPtr.Add(data.Scan0, y * data.Stride), row.Length);
                }
            }
            finally
            {
                bitmap.UnlockBits(data);
            }

            bitmap.Save(path, ImageFormat.Png);
        }
    }
}
'@

$systemDrawingAssembly = [System.Drawing.Bitmap].Assembly.Location

Add-Type `
    -TypeDefinition $pngWriterSource `
    -Language CSharp `
    -ReferencedAssemblies $systemDrawingAssembly

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
    $psi.WorkingDirectory = "C:\__Daten\Dev\__LumaScope\LumaScope"
    $psi.EnvironmentVariables["PATH"] = "C:\__Daten\Dev\__LumaScope\LumaScope\dist\executables\win-unpacked\resources\osmand\helpers;C:\Users\walter2\AppData\Local\Microsoft\WindowsApps\Microsoft.PowerShell_8wekyb3d8bbwe;C:\Program Files\Python314\Scripts\;C:\Program Files\Python314\;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\libnvvp;C:\WINDOWS\system32;C:\WINDOWS;C:\WINDOWS\System32\Wbem;C:\WINDOWS\System32\WindowsPowerShell\v1.0\;C:\WINDOWS\System32\OpenSSH\;C:\Program Files\Microsoft SQL Server\150\Tools\Binn\;C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\;C:\WINDOWS\system32;C:\WINDOWS;C:\WINDOWS\System32\Wbem;C:\WINDOWS\System32\WindowsPowerShell\v1.0\;C:\WINDOWS\System32\OpenSSH\;C:\Program Files\Calibre2\;C:\Program Files\NVIDIA Corporation\NVIDIA App\NvDLISR;C:\Program Files (x86)\NVIDIA Corporation\PhysX\Common;C:\Program Files\NVIDIA Corporation\Nsight Compute 2025.2.1\;C:\Program Files (x86)\Windows Kits\10\Windows Performance Toolkit\;C:\Program Files\tev\bin\;C:\Program Files\dotnet\;C:\Program Files\Microsoft SQL Server\170\Tools\Binn\;C:\Program Files\IDM Computer Solutions\UltraEdit;C:\Program Files\WSL\;C:\Program Files\nodejs\;C:\Program Files\Git\cmd;C:\Users\walter2\AppData\Local\Microsoft\WindowsApps;C:\__Daten;C:\Program Files (x86)\Android\android-sdk\platform-tools;C:\Users\walter2\.dotnet\tools;C:\Users\walter2\.lmstudio\bin;C:\Users\walter2\.dotnet\tools;C:\Users\walter2\.deno\bin;C:\Users\walter2\.dotnet\tools;C:\Users\walter2\AppData\Roaming\Python\Python314\Scripts;C:\Users\walter2\AppData\Roaming\Python\Python314"
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
        width = 256
        height = 256
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

    if ($pixelFormat -ne 1) {
        throw "Nicht unterstuetztes Pixel-Format: $pixelFormat (erwartet RGBA8 = 1)"
    }

    if ($stride -lt ($width * 4)) {
        throw "Stride ist kleiner als Breite x 4: $stride"
    }

    Write-Log "Reading RGBA payload..."

    [byte[]]$pixels = Read-ExactTimeout $outputStream ([int]$payloadLength) 60

    [System.IO.File]::WriteAllBytes($rgba, $pixels)

    Write-Log "RGBA file written: $rgba"
    Write-Log "Received bytes: $($pixels.Length)"

    [AtlasPngWriter]::Save($png, $pixels, [int]$width, [int]$height, [int]$stride)
    Write-Log "PNG file written: $png ($($width)x$($height))"

    $unique = @{}

    for ($i = 0; $i -lt $pixels.Length; $i += 4096) {
        $end = [Math]::Min($i + 3, $pixels.Length - 1)
        $key = "$($pixels[$i]),$($pixels[$i+1]),$($pixels[$i+2]),$($pixels[$end])"
        $unique[$key] = $true
    }

    Write-Log "Unique sampled RGBA values: $($unique.Count)"

    Write-Log "Frame empfangen. Sichtbares Kartenfenster bleibt 5 Sekunden ge�ffnet."
    Start-Sleep -Seconds 5

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
    Write-Host "PNG-Datei:       $png"
    Write-Host ""
    Read-Host "Test beendet. Enter zum Schliessen"
}

