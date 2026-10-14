param([Parameter(Mandatory)][ValidateSet('x64','arm64')][string]$Architecture)
$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {throw 'GitHub-hosted runners only'}
if([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant() -ne $Architecture) {throw 'Native OS architecture mismatch'}
$workspace=$env:GITHUB_WORKSPACE
$evidence=Join-Path $workspace 'evidence'
New-Item -ItemType Directory -Path $evidence | Out-Null
Start-Transcript -Path (Join-Path $evidence 'setup.log')
try {
  $deps=Join-Path $env:RUNNER_TEMP 'postgres-baseline-deps'
  New-Item -ItemType Directory -Path $deps | Out-Null
  $archive=Join-Path $deps 'winflex.zip'
  Invoke-WebRequest -Uri 'https://github.com/lexxmark/winflexbison/releases/download/v2.5.25/win_flex_bison-2.5.25.zip' -OutFile $archive
  if((Get-FileHash -LiteralPath $archive).Hash -ne '8D324B62BE33604B2C45AD1DD34AB93D722534448F55A16CA7292DE32B6AC135') {throw 'WinFlexBison checksum mismatch'}
  $flex=Join-Path $deps 'winflex'
  Expand-Archive -LiteralPath $archive -DestinationPath $flex
  $vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
  $vs=& $vswhere -latest -products '*' -property installationPath
  if($LASTEXITCODE -ne 0 -or -not $vs) {throw 'Visual Studio not found'}
  Import-Module (Join-Path $vs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
  Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments "-arch=$Architecture -host_arch=$Architecture"
  $env:PATH="$flex;C:/Strawberry/perl/bin;$env:PATH"
  & python -m pip install meson==1.11.2 ninja==1.13.0
  if($LASTEXITCODE -ne 0) {throw 'Build tools installation failed'}
  & python (Join-Path $PSScriptRoot 'windows-baseline.py') --arch $Architecture
  if($LASTEXITCODE -ne 0) {throw 'Baseline verification failed; inspect evidence'}
} finally {Stop-Transcript}
