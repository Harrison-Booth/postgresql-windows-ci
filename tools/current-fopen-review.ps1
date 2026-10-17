param([Parameter(Mandatory)][ValidateSet('x64','arm64')][string]$Architecture)
$ErrorActionPreference='Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {throw 'Hosted execution required'}
if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant() -ne $Architecture) {throw 'Native architecture mismatch'}
New-Item -ItemType Directory -Path "$env:GITHUB_WORKSPACE/evidence" | Out-Null
Start-Transcript -Path "$env:GITHUB_WORKSPACE/evidence/runner.log"
try {
 $taskVsWhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
 $taskVs=& $taskVsWhere -latest -products '*' -property installationPath
 if (-not $taskVs) {throw 'Visual Studio missing'}
 Import-Module (Join-Path $taskVs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
 Enter-VsDevShell -VsInstallPath $taskVs -SkipAutomaticLocation -DevCmdArguments "-arch=$Architecture -host_arch=$Architecture"
 $taskFlex=Join-Path $env:RUNNER_TEMP 'fopen-winflex'
 New-Item -ItemType Directory -Path $taskFlex | Out-Null
 $taskArchive=Join-Path $taskFlex 'winflex.zip'
 Invoke-WebRequest -Uri 'https://github.com/lexxmark/winflexbison/releases/download/v2.5.25/win_flex_bison-2.5.25.zip' -OutFile $taskArchive
 if ((Get-FileHash -LiteralPath $taskArchive).Hash -ne '8D324B62BE33604B2C45AD1DD34AB93D722534448F55A16CA7292DE32B6AC135') {throw 'WinFlexBison hash differs'}
 Expand-Archive -LiteralPath $taskArchive -DestinationPath (Join-Path $taskFlex 'bin')
 $env:PATH="$(Join-Path $taskFlex 'bin');C:/Strawberry/perl/bin;C:/Strawberry/c/bin;$env:PATH"
 & perl -MIPC::Run -MTest::More -e 'print "Perl TAP dependencies available\n"'
 if ($LASTEXITCODE -ne 0) {throw 'Perl TAP dependencies unavailable'}
 & python -m pip install meson==1.11.2 ninja==1.13.0
 if ($LASTEXITCODE -ne 0) {throw 'Meson installation failed'}
 $env:TEST_ARCH=$Architecture
 & python (Join-Path $PSScriptRoot 'test-current-fopen.py')
 if ($LASTEXITCODE -ne 0) {throw 'Current-master fopen controls failed'}
} finally {Stop-Transcript}
