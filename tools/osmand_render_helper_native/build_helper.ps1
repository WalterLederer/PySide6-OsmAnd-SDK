param(
    [string]$QtRoot = "C:\Qt\6.10.1\mingw_64",
    [string]$MinGWRoot = "C:\Qt\Tools\mingw1310_64",
    [ValidateSet("Debug", "Release", "RelWithDebInfo", "MinSizeRel")]
    [string]$BuildType = "Release",
    [string]$CMakeExe = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
    [string]$Generator = "MinGW Makefiles",
    [string]$GitBashExe = "C:\Program Files\Git\bin\bash.exe"
)

$ErrorActionPreference = "Stop"
git config --global core.longpaths true

function Assert-Exists {
    param([Parameter(Mandatory = $true)][string]$PathToCheck)
    if (-not (Test-Path $PathToCheck)) {
        throw "Required path does not exist: $PathToCheck"
    }
}


Assert-Exists $GitBashExe

$gitBin = Split-Path -Parent $GitBashExe
$gitUsrBin = Join-Path (Split-Path -Parent $gitBin) 'usr\bin'

# Git Bash muss vor WindowsApps stehen.
$env:PATH = "$gitBin;$gitUsrBin;$QtRoot\bin;$MinGWRoot\bin;$env:PATH"

$resolvedBash = (Get-Command bash.exe -ErrorAction Stop).Source

if ($resolvedBash -ne $GitBashExe) {
    throw "Wrong Bash executable resolved: $resolvedBash"
}

Write-Host "Using Git Bash: $resolvedBash"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "..\.."))
$vendorRoot = Join-Path $repoRoot "vendor\osmand"
$buildRoot = Join-Path $projectRoot "build"

Assert-Exists $projectRoot
Assert-Exists $vendorRoot
Assert-Exists (Join-Path $vendorRoot "core\CMakeLists.txt")
Assert-Exists (Join-Path $vendorRoot "core-legacy\CMakeLists.txt")
Assert-Exists $QtRoot
Assert-Exists $MinGWRoot
Assert-Exists $CMakeExe

New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

$env:PATH = "$($QtRoot)\bin;$($MinGWRoot)\bin;$env:PATH"

& $CMakeExe `
    -S $projectRoot `
    -B $buildRoot `
    -G $Generator `
    "-DCMAKE_BUILD_TYPE=$BuildType" `
    "-DCMAKE_PREFIX_PATH=$QtRoot" `
    "-DCMAKE_C_COMPILER=$MinGWRoot\bin\gcc.exe" `
    "-DCMAKE_CXX_COMPILER=$MinGWRoot\bin\g++.exe" `
    "-DQT_ROOT=$QtRoot" `
    "-DMINGW_ROOT=$MinGWRoot" `
    "-DOSMAND_VENDOR_ROOT=$vendorRoot"
if ($LASTEXITCODE -ne 0) {
    throw "CMake configure failed with exit code $LASTEXITCODE"
}

& $CMakeExe --build $buildRoot --config $BuildType --target osmand_render_helper osmand_native_widget
if ($LASTEXITCODE -ne 0) {
    throw "CMake build failed with exit code $LASTEXITCODE"
}

Write-Host "Helper built at: $(Join-Path $projectRoot 'dist\osmand_render_helper.exe')"
if (Test-Path (Join-Path $projectRoot 'dist\osmand_native_widget.dll')) {
    Write-Host "Native widget built at: $(Join-Path $projectRoot 'dist\osmand_native_widget.dll')"
}
if (Test-Path (Join-Path $projectRoot 'dist\libosmand_native_widget.dll')) {
    Write-Host "Native widget built at: $(Join-Path $projectRoot 'dist\libosmand_native_widget.dll')"
}
