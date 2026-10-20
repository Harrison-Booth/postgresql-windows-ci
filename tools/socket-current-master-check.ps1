$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted'){throw 'Standard GitHub-hosted runners only'}
$taskArch=$env:TEST_HOST_ARCH
if($taskArch -notin @('x64','arm64')){throw 'Explicit host architecture required'}
if([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant() -ne $taskArch){throw 'Native host architecture mismatch'}
$env:PATH="C:/Strawberry/perl/bin;C:/Strawberry/c/bin;C:/Program Files/Git/usr/bin;$env:PATH"
New-Item -ItemType Directory -Path evidence | Out-Null
Start-Transcript -Path evidence/runner.log
try {
 $vsRoot=[Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
 $vswhere=Join-Path $vsRoot 'Microsoft Visual Studio/Installer/vswhere.exe'
 $vs=& $vswhere -latest -products '*' -property installationPath
 if($LASTEXITCODE -ne 0 -or -not $vs){throw 'Visual Studio unavailable'}
 Import-Module (Join-Path $vs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
 Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments "-arch=$taskArch -host_arch=$taskArch"
 $deps=Join-Path $env:RUNNER_TEMP 'socket-current-master-dependencies'
 New-Item -ItemType Directory -Path $deps | Out-Null
 $archive=Join-Path $deps 'winflex.zip'
 Invoke-WebRequest 'https://github.com/lexxmark/winflexbison/releases/download/v2.5.25/win_flex_bison-2.5.25.zip' -OutFile $archive
 if((Get-FileHash -LiteralPath $archive).Hash -ne '8D324B62BE33604B2C45AD1DD34AB93D722534448F55A16CA7292DE32B6AC135'){throw 'WinFlexBison checksum mismatch'}
 $flex=Join-Path $deps 'winflex'
 Expand-Archive -LiteralPath $archive -DestinationPath $flex
 $env:PATH="$flex;$env:PATH"
 python -m pip install meson==1.11.2 ninja==1.13.0
 if($LASTEXITCODE -ne 0){throw 'Meson or Ninja installation failed'}
 & C:/Strawberry/perl/bin/perl.exe -MIPC::Run -e 1
 if($LASTEXITCODE -ne 0){throw 'IPC::Run unavailable'}
 python ci/tools/socket-current-master-check.py --arch $taskArch
 if($LASTEXITCODE -ne 0){throw 'Socket current-master validation failed'}
}finally{Stop-Transcript}
