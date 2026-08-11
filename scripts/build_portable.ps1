[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$SourcePythonRoot = Join-Path $ProjectRoot "python_embed"
$SourcePython = Join-Path $SourcePythonRoot "python.exe"
$RequirementsLock = Join-Path $ProjectRoot "requirements.lock"
$BuildRoot = Join-Path $ProjectRoot "build"
$Stage = Join-Path $BuildRoot ".HFDM-portable.next"
$Output = Join-Path $BuildRoot "HFDM-portable"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Assert-BuildDirectory {
    param([string]$Path, [string[]]$AllowedNames)
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $Parent = [System.IO.DirectoryInfo]::new($FullPath).Parent.FullName
    $Name = [System.IO.Path]::GetFileName($FullPath)
    if ($Parent -ne $BuildRoot -or $Name -notin $AllowedNames) {
        throw "Refusing to manage unexpected build directory: $FullPath"
    }
    return $FullPath
}

function Remove-BuildDirectory {
    param([string]$Path, [string[]]$AllowedNames)
    $SafePath = Assert-BuildDirectory -Path $Path -AllowedNames $AllowedNames
    if (Test-Path -LiteralPath $SafePath) {
        $Item = Get-Item -LiteralPath $SafePath -Force
        if ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to remove a reparse point: $SafePath"
        }
        Remove-Item -LiteralPath $SafePath -Recurse -Force -ErrorAction Stop
    }
}

if (-not (Test-Path -LiteralPath $SourcePython)) {
    throw "Embedded Python was not found: $SourcePython"
}
if (-not (Test-Path -LiteralPath $RequirementsLock)) {
    throw "Runtime lock was not found. Run manage_runtime.bat lock first."
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "app\frontend\dist\index.html"))) {
    throw "Built frontend was not found. Build app/frontend before creating a portable release."
}

if (-not (Test-Path -LiteralPath $BuildRoot)) {
    New-Item -ItemType Directory -Path $BuildRoot -ErrorAction Stop | Out-Null
}
Remove-BuildDirectory -Path $Stage -AllowedNames @(".HFDM-portable.next")
New-Item -ItemType Directory -Path $Stage -ErrorAction Stop | Out-Null

try {
    $RuntimeTarget = New-Item -ItemType Directory -Path (Join-Path $Stage "python_embed") -ErrorAction Stop
    $PackageTarget = New-Item -ItemType Directory -Path (Join-Path $Stage "packages") -ErrorAction Stop
    New-Item -ItemType Directory -Path (Join-Path $Stage "app\src") -Force -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Stage "app\frontend") -Force -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Stage "data") -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Stage "download") -ErrorAction Stop | Out-Null

    $PythonTag = (& $SourcePython -c "import sys; print(f'python{sys.version_info.major}{sys.version_info.minor}')")
    if ($LASTEXITCODE -ne 0 -or -not $PythonTag) {
        throw "Could not determine the embedded Python tag."
    }
    $CoreNames = @("python.exe", "pythonw.exe", "python3.dll", "$PythonTag.dll", "$PythonTag.zip", "LICENSE.txt")
    foreach ($Name in $CoreNames) {
        $Source = Join-Path $SourcePythonRoot $Name
        if (-not (Test-Path -LiteralPath $Source)) {
            throw "Required embedded Python file was not found: $Source"
        }
        Copy-Item -LiteralPath $Source -Destination $RuntimeTarget.FullName -ErrorAction Stop
    }
    Get-ChildItem -LiteralPath $SourcePythonRoot -File | Where-Object {
        $_.Extension -in @(".pyd", ".dll")
    } | Copy-Item -Destination $RuntimeTarget.FullName -ErrorAction Stop

    $Pth = @("$PythonTag.zip", ".", "..\packages", "..\app\src", "import site")
    [System.IO.File]::WriteAllLines((Join-Path $RuntimeTarget.FullName "$PythonTag._pth"), $Pth, $Utf8NoBom)

    & $SourcePython -m pip install --disable-pip-version-check --only-binary=:all: --no-compile `
        --target $PackageTarget.FullName -r $RequirementsLock
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install locked runtime dependencies into the portable build."
    }

    Copy-Item -LiteralPath (Join-Path $ProjectRoot "app\app.py") -Destination (Join-Path $Stage "app") -ErrorAction Stop
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "app\src\hfdm") -Destination (Join-Path $Stage "app\src") -Recurse -ErrorAction Stop
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "app\frontend\dist") -Destination (Join-Path $Stage "app\frontend") -Recurse -ErrorAction Stop
    foreach ($Name in @("start_service.bat", "README.md", "LICENSE", "requirements.txt", "requirements.lock")) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $Stage -ErrorAction Stop
    }

    $Version = (& $SourcePython -c "import hfdm; print(hfdm.__version__)")
    if ($LASTEXITCODE -ne 0 -or -not $Version) {
        throw "Could not determine HFDM version."
    }
    $Manifest = [ordered]@{
        name = "HFDM"
        version = $Version
        python = (& $SourcePython -c "import sys; print(sys.version.split()[0])")
        platform = "windows-x64"
        requirements_sha256 = (Get-FileHash -LiteralPath $RequirementsLock -Algorithm SHA256).Hash.ToLowerInvariant()
        built_at = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $Stage "runtime-manifest.json"), $Manifest, $Utf8NoBom)

    & cmd.exe /d /c (Join-Path $Stage "start_service.bat") --check
    if ($LASTEXITCODE -ne 0) {
        throw "Portable startup check failed."
    }

    Remove-BuildDirectory -Path $Output -AllowedNames @("HFDM-portable")
    Move-Item -LiteralPath $Stage -Destination $Output -ErrorAction Stop

    $Zip = Join-Path $BuildRoot "HFDM-$Version-windows-x64.zip"
    if (Test-Path -LiteralPath $Zip) {
        Remove-Item -LiteralPath $Zip -Force -ErrorAction Stop
    }
    Compress-Archive -Path (Join-Path $Output "*") -DestinationPath $Zip -CompressionLevel Optimal -ErrorAction Stop
    Write-Host "[OK] Portable folder: $Output"
    Write-Host "[OK] Portable archive: $Zip"
}
finally {
    Remove-BuildDirectory -Path $Stage -AllowedNames @(".HFDM-portable.next")
}
