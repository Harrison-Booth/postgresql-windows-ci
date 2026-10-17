param([Parameter(Mandatory)][ValidateSet('x64','arm64')][string]$Architecture,[switch]$FixturesOnly)
$ErrorActionPreference='Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {throw 'Standard GitHub-hosted execution required'}
if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant() -ne $Architecture) {throw 'Native architecture mismatch'}
$taskWorkspace=$env:GITHUB_WORKSPACE
$taskEvidence=Join-Path $taskWorkspace 'evidence'
New-Item -ItemType Directory -Path $taskEvidence | Out-Null
Start-Transcript -Path (Join-Path $taskEvidence 'runner.log')
try {
 $taskVsWhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
 $taskVs=& $taskVsWhere -latest -products '*' -property installationPath
 if (-not $taskVs) {throw 'Visual Studio missing'}
 Import-Module (Join-Path $taskVs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
 Enter-VsDevShell -VsInstallPath $taskVs -SkipAutomaticLocation -DevCmdArguments "-arch=$Architecture -host_arch=$Architecture"
 $taskDeps=Join-Path $env:RUNNER_TEMP 'meson-v4-review-dependencies'
 New-Item -ItemType Directory -Path $taskDeps | Out-Null
 $taskPkgArchive=Join-Path $taskDeps 'pkgconf.pkg.tar.zst'
 Invoke-WebRequest -Uri 'https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-pkgconf-1~3.0.7-2-any.pkg.tar.zst' -OutFile $taskPkgArchive
 if ((Get-FileHash -LiteralPath $taskPkgArchive).Hash -ne '754CEB500A1289DC78BFD45E26E2098414D1DCCF817ABDC4B9537693F8595AC0') {throw 'pkgconf archive hash differs'}
 & tar -xf $taskPkgArchive -C $taskDeps
 if ($LASTEXITCODE -ne 0) {throw 'pkgconf extraction failed'}
 $taskPkg=Join-Path $taskDeps 'ucrt64/bin/pkg-config.exe'
 & $taskPkg --version
 if ($LASTEXITCODE -ne 0) {throw 'pkgconf not runnable'}
 $taskFlexArchive=Join-Path $taskDeps 'winflex.zip'
 Invoke-WebRequest -Uri 'https://github.com/lexxmark/winflexbison/releases/download/v2.5.25/win_flex_bison-2.5.25.zip' -OutFile $taskFlexArchive
 if ((Get-FileHash -LiteralPath $taskFlexArchive).Hash -ne '8D324B62BE33604B2C45AD1DD34AB93D722534448F55A16CA7292DE32B6AC135') {throw 'WinFlexBison hash differs'}
 Expand-Archive -LiteralPath $taskFlexArchive -DestinationPath (Join-Path $taskDeps 'winflex')
 $env:PATH="$(Join-Path $taskDeps 'winflex');$taskDeps/ucrt64/bin;$env:PATH"
 & python -m pip install ninja==1.13.0
 if ($LASTEXITCODE -ne 0) {throw 'Ninja install failed'}
 $taskInput=Join-Path $taskWorkspace 'ci/tracking/meson-v4-review'
 $taskManifest=Get-Content (Join-Path $taskInput 'manifest.json') -Raw | ConvertFrom-Json
 foreach ($taskRecord in $taskManifest.inputs) {
  $taskActual=(Get-FileHash -LiteralPath (Join-Path $taskInput $taskRecord.file)).Hash.ToLowerInvariant()
  if ($taskActual -ne $taskRecord.sha256) {throw "Changed reviewed input: $($taskRecord.file)"}
 }
 & git -C (Join-Path $taskWorkspace 'postgres') apply (Join-Path $taskInput 'author-v4.patch')
 if ($LASTEXITCODE -ne 0) {throw 'Exact author v4 application failed'}
 $taskFixtureFailures=@()
 foreach ($taskVersion in @('0.57.2','1.11.2')) {
  $taskToolRoot=Join-Path $taskDeps "meson-$taskVersion"
  & python -m pip install "meson==$taskVersion" --target $taskToolRoot
  if ($LASTEXITCODE -ne 0) {throw "Meson $taskVersion installation failed"}
  $taskLauncher=Join-Path $taskDeps "meson-$taskVersion.py"
  "import sys; sys.path.insert(0, r'$taskToolRoot'); from mesonbuild.mesonmain import main; raise SystemExit(main())" | Set-Content -LiteralPath $taskLauncher -Encoding utf8
  foreach ($taskVariant in @('author','cmake-list-fix','cmake-list-and-space-fix')) {
   $taskExtra=@()
   if ($taskVariant -eq 'cmake-list-fix') {$taskExtra=@('--cmake-list-fix')}
   if ($taskVariant -eq 'cmake-list-and-space-fix') {$taskExtra=@('--cmake-list-fix','--cmake-space-fix')}
   & python (Join-Path $PSScriptRoot 'test-author-v4.py') --source (Join-Path $taskWorkspace 'postgres') --meson $taskLauncher --pkgconfig $taskPkg --output (Join-Path $taskEvidence "export-$taskVersion-$taskVariant") --native @taskExtra
   if ($LASTEXITCODE -ne 0) {$taskFixtureFailures += "$taskVersion/$taskVariant"}
  }
 }
 if (-not $FixturesOnly) {
 & python (Join-Path $PSScriptRoot 'test-author-v4-pg-build.py') --workspace $taskWorkspace --architecture $Architecture --meson (Join-Path $taskDeps 'meson-1.11.2.py')
 if ($LASTEXITCODE -ne 0) {throw 'Actual PostgreSQL include-order comparison failed its expected controls'}
 }
 if ($taskFixtureFailures.Count -ne 0) {throw "Unexpected configuration/control failures: $($taskFixtureFailures -join ', ')"}
 @{run_id=$env:GITHUB_RUN_ID;revision=$env:GITHUB_SHA;architecture=$Architecture;pg_include_order_skipped=[bool]$FixturesOnly;retained_pg_include_order_run='39065789528';scope='Diagnostic exact author-v4 export matrix. PostgreSQL include-order comparison runs only when FixturesOnly is false; inspect the explicit skip flag. Green means controls ran; individual v4 macro/header failures must be inspected. No full server/PGXS/SSL/ICU runtime claim.'} | ConvertTo-Json | Set-Content (Join-Path $taskEvidence 'completion.json')
} finally {Stop-Transcript}
