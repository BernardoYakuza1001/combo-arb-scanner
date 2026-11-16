$ProgressPreference = 'SilentlyContinue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip = Join-Path $here 'node.zip'; $tmp = Join-Path $here '.nodetmp'; $dst = Join-Path $here '.node'
Invoke-WebRequest 'https://nodejs.org/dist/v20.18.1/node-v20.18.1-win-x64.zip' -OutFile $zip
Expand-Archive $zip -DestinationPath $tmp -Force
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Move-Item (Join-Path $tmp 'node-v20.18.1-win-x64') $dst
Remove-Item $zip; Remove-Item $tmp -Recurse
Write-Output "node ok"
