param([Parameter(Mandatory)][ValidateSet('x64','arm64')][string]$Architecture)
$ErrorActionPreference='Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {throw 'Standard GitHub-hosted execution required'}
if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant() -ne $Architecture) {throw 'Native architecture mismatch'}
$taskWorkspace=$env:GITHUB_WORKSPACE
$taskEvidence=Join-Path $taskWorkspace 'evidence'
$taskInput=Join-Path $taskWorkspace 'ci/tracking/meson-v4-followups'
$taskPostgres=Join-Path $taskWorkspace 'postgres'
$taskBaseline=Join-Path $taskWorkspace 'postgres-v4-baseline'
New-Item -ItemType Directory -Path $taskEvidence | Out-Null
Start-Transcript -Path (Join-Path $taskEvidence 'runner.log')
try {
 $taskManifest=Get-Content (Join-Path $taskInput 'manifest.json') -Raw | ConvertFrom-Json
 $taskActualBase=(& git -C $taskPostgres rev-parse HEAD).Trim()
 if ($LASTEXITCODE -ne 0 -or $taskActualBase -ne $taskManifest.source_base) {throw "Unexpected PostgreSQL base: $taskActualBase"}
 foreach ($taskRecord in @($taskManifest.prerequisite)+@($taskManifest.followups)) {
  $taskActual=(Get-FileHash -LiteralPath (Join-Path $taskInput $taskRecord.file) -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($taskActual -ne $taskRecord.sha256) {throw "Changed reviewed input: $($taskRecord.file)"}
 }

 $taskVsWhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
 $taskVs=& $taskVsWhere -latest -products '*' -property installationPath
 if (-not $taskVs) {throw 'Visual Studio missing'}
 Import-Module (Join-Path $taskVs 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
 Enter-VsDevShell -VsInstallPath $taskVs -SkipAutomaticLocation -DevCmdArguments "-arch=$Architecture -host_arch=$Architecture"
 $taskDeps=Join-Path $env:RUNNER_TEMP 'meson-followup-dependencies'
 New-Item -ItemType Directory -Path $taskDeps | Out-Null
 $taskPkgArchive=Join-Path $taskDeps 'pkgconf.pkg.tar.zst'
 Invoke-WebRequest -Uri 'https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-pkgconf-1~3.0.7-2-any.pkg.tar.zst' -OutFile $taskPkgArchive -SkipHttpErrorCheck
 if ((Get-FileHash -LiteralPath $taskPkgArchive -Algorithm SHA256).Hash -ne '754CEB500A1289DC78BFD45E26E2098414D1DCCF817ABDC4B9537693F8595AC0') {throw 'pkgconf archive hash differs'}
 & tar -xf $taskPkgArchive -C $taskDeps
 if ($LASTEXITCODE -ne 0) {throw 'pkgconf extraction failed'}
 $taskPkg=Join-Path $taskDeps 'ucrt64/bin/pkg-config.exe'
 & $taskPkg --version
 if ($LASTEXITCODE -ne 0) {throw 'pkgconf not runnable'}
 $taskFlexArchive=Join-Path $taskDeps 'winflex.zip'
 Invoke-WebRequest -Uri 'https://github.com/lexxmark/winflexbison/releases/download/v2.5.25/win_flex_bison-2.5.25.zip' -OutFile $taskFlexArchive -SkipHttpErrorCheck
 if ((Get-FileHash -LiteralPath $taskFlexArchive -Algorithm SHA256).Hash -ne '8D324B62BE33604B2C45AD1DD34AB93D722534448F55A16CA7292DE32B6AC135') {throw 'WinFlexBison hash differs'}
 Expand-Archive -LiteralPath $taskFlexArchive -DestinationPath (Join-Path $taskDeps 'winflex')
 $env:PATH="$(Join-Path $taskDeps 'winflex');$taskDeps/ucrt64/bin;$env:PATH"
 & python -m pip install ninja==1.13.0
 if ($LASTEXITCODE -ne 0) {throw 'Ninja install failed'}

 & git -C $taskPostgres worktree add --detach $taskBaseline $taskManifest.source_base
 if ($LASTEXITCODE -ne 0) {throw 'Baseline worktree creation failed'}
 & git -C $taskPostgres config user.name 'PostgreSQL Windows CI'
 if ($LASTEXITCODE -ne 0) {throw 'Could not set the isolated CI commit name'}
 & git -C $taskPostgres config user.email 'postgresql-windows-ci@users.noreply.github.com'
 if ($LASTEXITCODE -ne 0) {throw 'Could not set the isolated CI commit email'}
 $taskPrerequisite=Join-Path $taskInput $taskManifest.prerequisite.file
 & git -C $taskBaseline am $taskPrerequisite
 if ($LASTEXITCODE -ne 0) {throw 'The formatted public v4 prerequisite did not apply with git am'}
 & git -C $taskPostgres am $taskPrerequisite
 if ($LASTEXITCODE -ne 0) {throw 'The formatted public v4 prerequisite did not apply to the candidate'}
 foreach ($taskRecord in $taskManifest.followups) {
  & git -C $taskPostgres am (Join-Path $taskInput $taskRecord.file)
  if ($LASTEXITCODE -ne 0) {throw "Follow-up did not apply with git am: $($taskRecord.file)"}
 }
 $taskCandidateHead=(& git -C $taskPostgres rev-parse HEAD).Trim()
 $taskBaselineHead=(& git -C $taskBaseline rev-parse HEAD).Trim()

 foreach ($taskVersion in @('0.57.2','1.11.2')) {
  $taskToolRoot=Join-Path $taskDeps "meson-$taskVersion"
  & python -m pip install "meson==$taskVersion" --target $taskToolRoot
  if ($LASTEXITCODE -ne 0) {throw "Meson $taskVersion installation failed"}
  $taskLauncher=Join-Path $taskDeps "meson-$taskVersion.py"
  "import sys; sys.path.insert(0, r'$taskToolRoot'); from mesonbuild.mesonmain import main; raise SystemExit(main())" | Set-Content -LiteralPath $taskLauncher -Encoding utf8
  & python (Join-Path $PSScriptRoot 'test-meson-followup-export.py') --source $taskPostgres --manifest (Join-Path $taskInput 'manifest.json') --meson $taskLauncher --pkgconfig $taskPkg --output (Join-Path $taskEvidence "export-$taskVersion")
  if ($LASTEXITCODE -ne 0) {throw "Export configuration fixture failed under Meson $taskVersion; preserve logs"}
  if ($taskVersion -eq '1.11.2') { $taskBuildMeson=$taskLauncher }
 }
 & python (Join-Path $PSScriptRoot 'test-meson-followup-pg-build.py') --workspace $taskWorkspace --architecture $Architecture --meson $taskBuildMeson --baseline $taskBaseline --candidate $taskPostgres
 if ($LASTEXITCODE -ne 0) {throw 'The exact patch-series PostgreSQL include-order controls failed; preserve logs'}
 $taskReceipt=@{
  run_id=$env:GITHUB_RUN_ID
  workflow_revision=$env:GITHUB_SHA
  architecture=$Architecture
  upstream_base=$taskActualBase
  baseline_head=$taskBaselineHead
  candidate_head=$taskCandidateHead
  prerequisite_sha256=$taskManifest.prerequisite.sha256
  followup_sha256=@($taskManifest.followups | ForEach-Object {$_.sha256})
  meson_versions=@('0.57.2','1.11.2')
  scope='Exact ordered git-am series, controlled CMake/pkg-config include export cases, and actual psql header-order canary builds. No full server, installed extension, provider, or PGXS runtime claim.'
 }
 $taskReceipt | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $taskEvidence 'completion.json') -Encoding utf8
} finally {Stop-Transcript}
