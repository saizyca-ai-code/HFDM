[CmdletBinding()]
param(
    [ValidateSet("help", "lock", "runtime", "dev", "check", "pip")]
    [string]$Action = "help",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PackageArguments
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot "python_embed\python.exe"
$Packages = Join-Path $ProjectRoot "packages"
$RuntimeInput = Join-Path $ProjectRoot "requirements.txt"
$RuntimeLock = Join-Path $ProjectRoot "requirements.lock"
$DevInput = Join-Path $ProjectRoot "requirements-dev.txt"
$DevLock = Join-Path $ProjectRoot "requirements-dev.lock"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Configure-EmbeddedPath {
    $PythonRoot = Split-Path -Parent $Python
    $PthFiles = @(Get-ChildItem -LiteralPath $PythonRoot -Filter "python*._pth" -File)
    $ZipFiles = @(Get-ChildItem -LiteralPath $PythonRoot -Filter "python*.zip" -File)
    if ($PthFiles.Count -ne 1 -or $ZipFiles.Count -ne 1) {
        throw "Expected exactly one python*._pth and one python*.zip in $PythonRoot"
    }
    $Lines = @(
        $ZipFiles[0].Name,
        ".",
        "..\packages",
        "..\app\src",
        "Lib",
        "Lib\site-packages",
        "Scripts",
        "import site"
    )
    [System.IO.File]::WriteAllLines($PthFiles[0].FullName, $Lines, $Utf8NoBom)
}

function Invoke-HfdmPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Embedded Python failed with exit code $LASTEXITCODE."
    }
}

function Assert-ManagedDirectory {
    param([string]$Path, [string[]]$AllowedNames)
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $Parent = [System.IO.DirectoryInfo]::new($FullPath).Parent.FullName
    $Name = [System.IO.Path]::GetFileName($FullPath)
    if ($Parent -ne $ProjectRoot -or $Name -notin $AllowedNames) {
        throw "Refusing to manage unexpected directory: $FullPath"
    }
    return $FullPath
}

function Remove-ManagedDirectory {
    param([string]$Path, [string[]]$AllowedNames)
    $SafePath = Assert-ManagedDirectory -Path $Path -AllowedNames $AllowedNames
    if (Test-Path -LiteralPath $SafePath) {
        $Item = Get-Item -LiteralPath $SafePath -Force
        if ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to remove a reparse point: $SafePath"
        }
        Remove-Item -LiteralPath $SafePath -Recurse -Force -ErrorAction Stop
    }
}

function Write-LockFile {
    param([string]$InputFile, [string]$OutputFile, [string]$Name)
    $LockRoot = Join-Path $ProjectRoot ".runtime"
    $Target = Join-Path $LockRoot "lock-$Name"
    $TargetFullPath = [System.IO.Path]::GetFullPath($Target)
    if ([System.IO.DirectoryInfo]::new($TargetFullPath).Parent.FullName -ne $LockRoot -or
        [System.IO.Path]::GetFileName($TargetFullPath) -notin @("lock-runtime", "lock-dev")) {
        throw "Refusing to manage unexpected lock directory: $TargetFullPath"
    }
    if (-not (Test-Path -LiteralPath $LockRoot)) {
        New-Item -ItemType Directory -Path $LockRoot -ErrorAction Stop | Out-Null
    }
    if (Test-Path -LiteralPath $TargetFullPath) {
        $Item = Get-Item -LiteralPath $TargetFullPath -Force
        if ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to remove a reparse point: $TargetFullPath"
        }
        Remove-Item -LiteralPath $TargetFullPath -Recurse -Force -ErrorAction Stop
    }
    New-Item -ItemType Directory -Path $TargetFullPath -ErrorAction Stop | Out-Null
    try {
        Invoke-HfdmPython -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
            "--no-compile", "--target", $TargetFullPath, "-r", $InputFile
        )
        $Frozen = @(& $Python -m pip freeze --path $TargetFullPath)
        if ($LASTEXITCODE -ne 0 -or $Frozen.Count -eq 0) {
            throw "Could not generate $Name dependency lock."
        }
        [Array]::Sort($Frozen, [System.StringComparer]::OrdinalIgnoreCase)
        [System.IO.File]::WriteAllLines($OutputFile, $Frozen, $Utf8NoBom)
        Write-Host "[OK] Wrote $OutputFile"
    }
    finally {
        if (Test-Path -LiteralPath $TargetFullPath) {
            Remove-Item -LiteralPath $TargetFullPath -Recurse -Force -ErrorAction Stop
        }
    }
}

function Sync-Packages {
    param([string]$RequirementsFile, [string]$Mode)
    $Next = Assert-ManagedDirectory -Path (Join-Path $ProjectRoot "packages.next") -AllowedNames @("packages.next")
    $Previous = Assert-ManagedDirectory -Path (Join-Path $ProjectRoot "packages.previous") -AllowedNames @("packages.previous")
    Remove-ManagedDirectory -Path $Next -AllowedNames @("packages.next")
    Remove-ManagedDirectory -Path $Previous -AllowedNames @("packages.previous")
    New-Item -ItemType Directory -Path $Next -ErrorAction Stop | Out-Null
    try {
        Invoke-HfdmPython -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
            "--no-compile", "--target", $Next, "-r", $RequirementsFile
        )
        $Metadata = [ordered]@{
            mode = $Mode
            python = (& $Python -c "import sys; print(sys.version.split()[0])")
            requirements = [System.IO.Path]::GetFileName($RequirementsFile)
            requirements_sha256 = (Get-FileHash -LiteralPath $RequirementsFile -Algorithm SHA256).Hash.ToLowerInvariant()
            generated_at = (Get-Date).ToUniversalTime().ToString("o")
        } | ConvertTo-Json
        [System.IO.File]::WriteAllText((Join-Path $Next "hfdm-packages.json"), $Metadata, $Utf8NoBom)

        if (Test-Path -LiteralPath $Packages) {
            Move-Item -LiteralPath $Packages -Destination $Previous -ErrorAction Stop
        }
        try {
            Move-Item -LiteralPath $Next -Destination $Packages -ErrorAction Stop
        }
        catch {
            if (Test-Path -LiteralPath $Previous -and -not (Test-Path -LiteralPath $Packages)) {
                Move-Item -LiteralPath $Previous -Destination $Packages -ErrorAction Stop
            }
            throw
        }
        Remove-ManagedDirectory -Path $Previous -AllowedNames @("packages.previous")
        Write-Host "[OK] Synchronized $Mode packages in $Packages"
    }
    finally {
        Remove-ManagedDirectory -Path $Next -AllowedNames @("packages.next")
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Embedded Python was not found: $Python"
}
Configure-EmbeddedPath

switch ($Action) {
    "lock" {
        Write-LockFile -InputFile $RuntimeInput -OutputFile $RuntimeLock -Name "runtime"
        Write-LockFile -InputFile $DevInput -OutputFile $DevLock -Name "dev"
    }
    "runtime" {
        $Source = if (Test-Path -LiteralPath $RuntimeLock) { $RuntimeLock } else { $RuntimeInput }
        Sync-Packages -RequirementsFile $Source -Mode "runtime"
    }
    "dev" {
        $Source = if (Test-Path -LiteralPath $DevLock) { $DevLock } else { $DevInput }
        Sync-Packages -RequirementsFile $Source -Mode "development"
    }
    "check" {
        Invoke-HfdmPython -Arguments @(
            "-c",
            "import fastapi, huggingface_hub, hfdm, uvicorn; print('HFDM:', hfdm.__file__); print('FastAPI:', fastapi.__file__)"
        )
        Invoke-HfdmPython -Arguments @("-m", "pytest", (Join-Path $ProjectRoot "app\tests"))
    }
    "pip" {
        if (-not $PackageArguments -or $PackageArguments.Count -eq 0) {
            throw "Usage: manage_runtime.bat pip PACKAGE [PACKAGE ...]"
        }
        if (-not (Test-Path -LiteralPath $Packages)) {
            New-Item -ItemType Directory -Path $Packages -ErrorAction Stop | Out-Null
        }
        $PipArguments = @(
            "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
            "--no-compile", "--upgrade", "--target", $Packages
        ) + $PackageArguments
        Invoke-HfdmPython -Arguments $PipArguments
        Write-Host "[OK] Updated development packages. Record intentional dependencies before locking."
    }
    default {
        Write-Host "HFDM runtime manager"
        Write-Host "  manage_runtime.bat lock       Resolve and write runtime/dev lock files"
        Write-Host "  manage_runtime.bat runtime    Rebuild packages from runtime lock"
        Write-Host "  manage_runtime.bat dev        Rebuild packages from development lock"
        Write-Host "  manage_runtime.bat check      Verify imports and run tests"
        Write-Host "  manage_runtime.bat pip PKG... Install or update development packages"
    }
}
