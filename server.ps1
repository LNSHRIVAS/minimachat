$port = 8081
$prefix = "http://localhost:$port/"
$root = Split-Path -Parent $PSCommandPath

# Reclaim port if a previous minima server hung (common after long pytest/GUI runs)
$myPid = $PID
$stale = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.ProcessId -ne $myPid -and $_.CommandLine -like '*server.ps1*' }
foreach ($p in $stale) {
  try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
}
if ($stale) { Start-Sleep -Milliseconds 800 }

try {
  $listener = New-Object System.Net.HttpListener
  $listener.Prefixes.Add($prefix)
  $listener.Start()
  Write-Host "minima running at http://localhost:$port" -ForegroundColor Green
} catch { Write-Host "Failed to start on port $port" -ForegroundColor Red; exit 1 }

Add-Type -AssemblyName System.Web

$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Read-RequestBodyUtf8($req) {
  $reader = New-Object System.IO.StreamReader($req.InputStream, $Utf8NoBom)
  try { return $reader.ReadToEnd() } finally { $reader.Close() }
}

function Write-BridgeStdin($process, [byte[]]$bytes) {
  $stream = $process.StandardInput.BaseStream
  if ($bytes -and $bytes.Length -gt 0) {
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
  }
  $process.StandardInput.Close()
}

function Invoke-PythonBridge($scriptPath, $arguments, $bodyJson) {
  $py = 'python'
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py
  $psi.Arguments = $arguments
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $p = [Diagnostics.Process]::Start($psi)
  if ($bodyJson) {
    $bytes = $Utf8NoBom.GetBytes([string]$bodyJson)
    Write-BridgeStdin $p $bytes
  } else {
    $p.StandardInput.Close()
  }
  $out = $p.StandardOutput.ReadToEnd()
  $p.WaitForExit()
  if ($out) { return $out | ConvertFrom-Json }
  return @{ ok = $false; error = 'empty response' }
}

function Invoke-SinceBridge($cmd, $bodyJson) {
  $script = Join-Path $root 'since_bridge.py'
  if (-not (Test-Path -LiteralPath $script)) { return @{ ok = $false; error = 'since_bridge.py missing' } }
  try {
    return Invoke-PythonBridge $script "`"$script`" $cmd" $bodyJson
  } catch {
    return @{ ok = $false; error = $_.Exception.Message }
  }
}

function Invoke-SearchBridge($bodyJson) {
  $script = Join-Path $root 'search_bridge.py'
  if (-not (Test-Path -LiteralPath $script)) { return @{ ok = $false; error = 'search_bridge.py missing' } }
  try {
    return Invoke-PythonBridge $script "`"$script`"" $bodyJson
  } catch {
    return @{ ok = $false; error = $_.Exception.Message }
  }
}

function Write-Json($res, $obj) {
  $json = ConvertTo-Json $obj -Compress -Depth 12
  $buf = [Text.Encoding]::UTF8.GetBytes($json)
  $res.ContentType = 'application/json; charset=utf-8'
  $res.OutputStream.Write($buf, 0, $buf.Length)
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  if (-not $ProcessId) { return }
  try {
    & taskkill /PID $ProcessId /T /F 2>$null | Out-Null
  } catch {
    try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch {}
  }
}

function Invoke-MinimaRequest {
  param([System.Net.HttpListenerContext]$Context)
  $context = $Context
  $req = $context.Request
  $res = $context.Response
  $local = $req.Url.LocalPath.ToLowerInvariant()

  try {
    if ($local -eq '/api/workspace') {
      $exports = Join-Path $root 'minima-exports'
      if (-not (Test-Path -LiteralPath $exports)) {
        New-Item -ItemType Directory -Path $exports -Force | Out-Null
      }
      $obj = @{ root = $root; exports = $exports; userprofile = $env:USERPROFILE }
      $json = ConvertTo-Json $obj -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/drives') {
      $drives = [IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady } | ForEach-Object { @{ name = $_.Name; label = $_.VolumeLabel } }
      $json = ConvertTo-Json $drives -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/list') {
      $path = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      if (-not $path) { throw 'path required' }
      if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        $hint = $env:USERPROFILE
        $errObj = @{ error = 'path not found'; path = $path; hint = $hint }
        $json = ConvertTo-Json $errObj -Compress
        $buf = [Text.Encoding]::UTF8.GetBytes($json)
        $res.StatusCode = 404
        $res.ContentType = 'application/json'
        $res.OutputStream.Write($buf, 0, $buf.Length)
      } else {
        $items = @(Get-ChildItem -LiteralPath $path -ErrorAction Stop | ForEach-Object {
          @{ name = $_.Name; type = if ($_.PSIsContainer) { 'dir' } else { 'file' }; size = if (-not $_.PSIsContainer) { $_.Length } else { $null } }
        })
        $json = ConvertTo-Json $items -Compress
        if (-not $json) { $json = '[]' }
        $buf = [Text.Encoding]::UTF8.GetBytes($json)
        $res.ContentType = 'application/json'
        $res.OutputStream.Write($buf, 0, $buf.Length)
      }
    } elseif ($local -eq '/api/fs/image') {
      $path = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      if (-not $path) { throw 'path required' }
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "not found: $path" }
      $ext = [IO.Path]::GetExtension($path).ToLowerInvariant()
      $allowed = @('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico')
      if ($allowed -notcontains $ext) { throw "unsupported image type: $ext" }
      $bytes = [IO.File]::ReadAllBytes($path)
      $map = @{ '.png' = 'image/png'; '.jpg' = 'image/jpeg'; '.jpeg' = 'image/jpeg'; '.gif' = 'image/gif'; '.webp' = 'image/webp'; '.svg' = 'image/svg+xml'; '.bmp' = 'image/bmp'; '.ico' = 'image/x-icon' }
      $res.ContentType = if ($map.ContainsKey($ext)) { $map[$ext] } else { 'application/octet-stream' }
      $res.ContentLength64 = $bytes.Length
      $res.Headers.Add('Cache-Control', 'private, max-age=3600')
      $res.OutputStream.Write($bytes, 0, $bytes.Length)
    } elseif ($local -eq '/api/fs/head') {
      $path = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      if (-not $path) { throw 'path required' }
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "not found: $path" }
      $linesParam = $req.QueryString['lines']
      $n = if ($linesParam) { [Math]::Min(40, [Math]::Max(1, [int]$linesParam)) } else { 10 }
      $fileHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.Substring(0, 16)
      $mtime = (Get-Item -LiteralPath $path).LastWriteTimeUtc.ToString('o')
      $allLines = @(Get-Content -LiteralPath $path -ErrorAction Stop)
      $totalLines = $allLines.Count
      $slice = @($allLines | Select-Object -First $n)
      $numbered = for ($i = 0; $i -lt $slice.Count; $i++) {
        ('{0,6}|{1}' -f ($i + 1), $slice[$i])
      }
      $content = ($numbered -join "`n")
      if ($content.Length -gt 4096) { $content = $content.Substring(0, 4096) + "`n...(truncated)" }
      $res.Headers.Add('X-Total-Lines', "$totalLines")
      $res.Headers.Add('X-Content-Hash', $fileHash)
      $res.Headers.Add('X-Mtime-Utc', $mtime)
      $res.Headers.Add('Access-Control-Expose-Headers', 'X-Total-Lines, X-Content-Hash, X-Mtime-Utc')
      $buf = [Text.Encoding]::UTF8.GetBytes($content)
      $res.ContentType = 'text/plain; charset=utf-8'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/mtime') {
      $path = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      if (-not $path) { throw 'path required' }
      if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
      $item = Get-Item -LiteralPath $path
      $hash = if ($item.PSIsContainer) { $null } else { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.Substring(0, 16) }
      $obj = @{
        ok = $true
        path = $path
        exists = $true
        type = if ($item.PSIsContainer) { 'dir' } else { 'file' }
        size = if (-not $item.PSIsContainer) { $item.Length } else { $null }
        mtime_utc = $item.LastWriteTimeUtc.ToString('o')
        hash = $hash
      }
      $json = ConvertTo-Json $obj -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/find') {
      $pattern = [System.Web.HttpUtility]::UrlDecode($req.QueryString['pattern'])
      $rootPath = [System.Web.HttpUtility]::UrlDecode($req.QueryString['root'])
      if (-not $pattern) { throw 'pattern required' }
      if (-not $rootPath) { $rootPath = (Split-Path -Parent $PSCommandPath) }
      if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) { throw "root not found: $rootPath" }
      $maxDepth = 6
      $depthParam = $req.QueryString['depth']
      if ($depthParam) { $maxDepth = [Math]::Min(12, [Math]::Max(1, [int]$depthParam)) }
      $results = @()
      $seenPaths = @{}
      $rootItem = Get-Item -LiteralPath $rootPath
      $stack = New-Object System.Collections.Stack
      $stack.Push(@{ Item = $rootItem; Depth = 0 })
      while ($stack.Count -gt 0 -and $results.Count -lt 30) {
        $frame = $stack.Pop()
        $cur = $frame.Item
        $depth = $frame.Depth
        try {
          $children = Get-ChildItem -LiteralPath $cur.FullName -ErrorAction SilentlyContinue
        } catch { continue }
        foreach ($child in $children) {
          if ($results.Count -ge 30) { break }
          if ($child.Name -like "*$pattern*" -and -not $seenPaths.ContainsKey($child.FullName)) {
            $seenPaths[$child.FullName] = $true
            $results += @{ name = $child.Name; path = $child.FullName; type = if ($child.PSIsContainer) { 'dir' } else { 'file' } }
          }
          if ($child.PSIsContainer -and $depth -lt $maxDepth) {
            $stack.Push(@{ Item = $child; Depth = ($depth + 1) })
          }
        }
      }
      $json = ConvertTo-Json $results -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/read') {
      $path = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      if (-not $path) { throw 'path required' }
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "not found: $path" }
      $offsetParam = $req.QueryString['offset']
      $limitParam = $req.QueryString['limit']
      if (-not $offsetParam -or -not $limitParam) { throw 'offset and limit required (max 160 lines)' }
      $offset = [Math]::Max(1, [int]$offsetParam)
      $limit = [Math]::Min(160, [Math]::Max(1, [int]$limitParam))
      $fileHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.Substring(0, 16)
      $lines = Get-Content -LiteralPath $path -ErrorAction Stop
      $totalLines = @($lines).Count
      $slice = @($lines | Select-Object -Skip ($offset - 1) -First $limit)
      $numbered = for ($i = 0; $i -lt $slice.Count; $i++) {
        $ln = $offset + $i
        ('{0,6}|{1}' -f $ln, $slice[$i])
      }
      $content = ($numbered -join "`n")
      if ($content.Length -gt 8192) { $content = $content.Substring(0, 8192) + "`n...(truncated)" }
      $returnedTo = if ($slice.Count -gt 0) { $offset + $slice.Count - 1 } else { $offset - 1 }
      $nextOffset = if ($returnedTo -lt $totalLines) { "$($returnedTo + 1)" } else { '' }
      $res.Headers.Add('X-Total-Lines', "$totalLines")
      $res.Headers.Add('X-Returned-From', "$offset")
      $res.Headers.Add('X-Returned-To', "$returnedTo")
      $res.Headers.Add('X-Next-Offset', $nextOffset)
      $res.Headers.Add('X-Content-Hash', $fileHash)
      $buf = [Text.Encoding]::UTF8.GetBytes($content)
      $res.ContentType = 'text/plain; charset=utf-8'
      $res.Headers.Add('Access-Control-Expose-Headers', 'X-Total-Lines, X-Returned-From, X-Returned-To, X-Next-Offset, X-Content-Hash')
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/grep') {
      $path = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      $pattern = [System.Web.HttpUtility]::UrlDecode($req.QueryString['pattern'])
      if (-not $path) { throw 'path required' }
      if (-not $pattern) { throw 'pattern required' }
      $limitParam = $req.QueryString['limit']
      $maxHits = if ($limitParam) { [Math]::Min(20, [Math]::Max(1, [int]$limitParam)) } else { 20 }
      $matches = @()
      try {
        $isDir = Test-Path -LiteralPath $path -PathType Container
      } catch { $isDir = $false }
      if ($isDir) {
        $files = Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
          Where-Object { $_.Length -lt 2000000 -and $_.Extension -notin '.png','.jpg','.jpeg','.gif','.webp','.ico','.pdf','.exe','.dll','.zip' }
        foreach ($f in $files) {
          if ($matches.Count -ge $maxHits) { break }
          try {
            $found = Select-String -LiteralPath $f.FullName -Pattern $pattern -ErrorAction SilentlyContinue
          } catch { $found = $null }
          foreach ($m in $found) {
            $preview = [string]$m.Line
            if ($preview.Length -gt 120) { $preview = $preview.Substring(0, 120) }
            $matches += @{
              path = $f.FullName
              line = $m.LineNumber
              preview = $preview
            }
            if ($matches.Count -ge $maxHits) { break }
          }
        }
      } else {
        $found = Select-String -LiteralPath $path -Pattern $pattern -ErrorAction Stop
        foreach ($m in $found) {
          if ($matches.Count -ge $maxHits) { break }
          $preview = [string]$m.Line
          if ($preview.Length -gt 120) { $preview = $preview.Substring(0, 120) }
          $matches += @{
            path = $path
            line = $m.LineNumber
            preview = $preview
          }
        }
      }
      $json = ConvertTo-Json $matches -Compress -Depth 6
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/search') {
      $query = [System.Web.HttpUtility]::UrlDecode($req.QueryString['query'])
      $rootPath = [System.Web.HttpUtility]::UrlDecode($req.QueryString['root'])
      if (-not $query) { throw 'query required' }
      if (-not $rootPath) { $rootPath = 'D:\' }
      $results = Get-ChildItem -LiteralPath $rootPath -Recurse -ErrorAction SilentlyContinue -Include "*$query*" | ForEach-Object { @{ name = $_.Name; path = $_.FullName; type = if ($_.PSIsContainer) { 'dir' } else { 'file' } } } | Select-Object -First 100
      $json = ConvertTo-Json $results -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/write' -and $req.HttpMethod -eq 'POST') {
      $reader = New-Object System.IO.StreamReader($req.InputStream, $req.ContentEncoding)
      $raw = $reader.ReadToEnd()
      $body = $raw | ConvertFrom-Json
      if (-not $body.path) { throw 'path required' }
      if ($null -eq $body.content) { $body.content = '' }
      $overwrite = $false
      if ($null -ne $body.overwrite) { $overwrite = [bool]$body.overwrite }
      if ((Test-Path -LiteralPath $body.path) -and -not $overwrite) {
        $res.StatusCode = 409
        $conflict = @{ ok = $false; error = 'file exists — set overwrite:true to replace'; path = $body.path } | ConvertTo-Json -Compress
        $buf = [Text.Encoding]::UTF8.GetBytes($conflict)
        $res.ContentType = 'application/json'
        $res.OutputStream.Write($buf, 0, $buf.Length)
        return
      }
      $dir = [System.IO.Path]::GetDirectoryName($body.path)
      if ($dir -and -not [System.IO.Directory]::Exists($dir)) {
        [System.IO.Directory]::CreateDirectory($dir) | Out-Null
      }
      [IO.File]::WriteAllText($body.path, [string]$body.content, [Text.UTF8Encoding]::new($false))
      $out = @{ ok = $true; path = $body.path; bytes = ([Text.Encoding]::UTF8.GetByteCount([string]$body.content)) }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/append' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      $body = $raw | ConvertFrom-Json
      if (-not $body.path) { throw 'path required' }
      if ($null -eq $body.content) { $body.content = '' }
      $dir = [System.IO.Path]::GetDirectoryName($body.path)
      if ($dir -and -not [System.IO.Directory]::Exists($dir)) {
        [System.IO.Directory]::CreateDirectory($dir) | Out-Null
      }
      $bytes = [Text.Encoding]::UTF8.GetBytes([string]$body.content)
      $stream = [System.IO.File]::Open($body.path, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
      try {
        $stream.Write($bytes, 0, $bytes.Length)
      } finally {
        $stream.Close()
      }
      $out = @{ ok = $true; path = $body.path; appended = $bytes.Length; size = (Get-Item -LiteralPath $body.path).Length }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/edit' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      $body = $raw | ConvertFrom-Json
      if (-not $body.path) { throw 'path required' }
      $path = [string]$body.path
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "not found: $path" }
      $edits = @($body.edits)
      if (-not $edits -or $edits.Count -eq 0) { throw 'edits required' }
      $content = [IO.File]::ReadAllText($path, [Text.UTF8Encoding]::new($false))
      if ($body.expected_hash) {
        $curHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.Substring(0, 16)
        if ([string]$body.expected_hash -ne $curHash) { throw 'stale file version (hash mismatch)' }
      }
      $applied = New-Object System.Collections.Generic.List[object]
      foreach ($e in $edits) {
        $op = if ($e.op) { [string]$e.op } else { [string]$e.type }
        switch ($op) {
          'replace' {
            $old = [string]$e.old_string
            if (-not $old) { throw 'old_string required for replace' }
            if ($null -eq $e.new_string) { $new = '' } else { $new = [string]$e.new_string }
            if (-not $content.Contains($old)) { throw "old_string not found in file" }
            $matchCount = ([regex]::Matches($content, [regex]::Escape($old))).Count
            if ($e.replace_all) {
              $content = $content.Replace($old, $new)
              $applied.Add(@{ op = 'replace'; count = $matchCount; replace_all = $true })
            } else {
              if ($matchCount -ne 1) { throw "old_string must match exactly once ($matchCount matches)" }
              $idx = $content.IndexOf($old)
              $content = $content.Substring(0, $idx) + $new + $content.Substring($idx + $old.Length)
              $applied.Add(@{ op = 'replace'; count = 1; replace_all = $false })
            }
          }
          'delete' {
            $old = [string]$e.old_string
            if (-not $old) { throw 'old_string required for delete' }
            if (-not $content.Contains($old)) { throw "old_string not found in file" }
            if ($e.replace_all) {
              $count = ([regex]::Matches($content, [regex]::Escape($old))).Count
              $content = $content.Replace($old, '')
              $applied.Add(@{ op = 'delete'; count = $count; replace_all = $true })
            } else {
              $idx = $content.IndexOf($old)
              $content = $content.Substring(0, $idx) + $content.Substring($idx + $old.Length)
              $applied.Add(@{ op = 'delete'; count = 1; replace_all = $false })
            }
          }
          'insert_after' {
            $anchor = [string]$e.anchor
            if (-not $anchor) { throw 'anchor required for insert_after' }
            if ($null -eq $e.content) { $ins = '' } else { $ins = [string]$e.content }
            $idx = $content.IndexOf($anchor)
            if ($idx -lt 0) { throw "anchor not found in file" }
            $pos = $idx + $anchor.Length
            $content = $content.Substring(0, $pos) + $ins + $content.Substring($pos)
            $applied.Add(@{ op = 'insert_after'; anchor = $anchor })
          }
          'insert_before' {
            $anchor = [string]$e.anchor
            if (-not $anchor) { throw 'anchor required for insert_before' }
            if ($null -eq $e.content) { $ins = '' } else { $ins = [string]$e.content }
            $idx = $content.IndexOf($anchor)
            if ($idx -lt 0) { throw "anchor not found in file" }
            $content = $content.Substring(0, $idx) + $ins + $content.Substring($idx)
            $applied.Add(@{ op = 'insert_before'; anchor = $anchor })
          }
          default { throw "unknown edit op: $op" }
        }
      }
      [IO.File]::WriteAllText($path, $content, [Text.UTF8Encoding]::new($false))
      $out = @{
        ok = $true
        path = $path
        bytes = ([Text.Encoding]::UTF8.GetByteCount($content))
        edits_applied = $applied
      }
      $json = ConvertTo-Json $out -Compress -Depth 6
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/mkdir' -and $req.HttpMethod -eq 'POST') {
      $reader = New-Object System.IO.StreamReader($req.InputStream, $req.ContentEncoding)
      $raw = $reader.ReadToEnd()
      $body = $raw | ConvertFrom-Json
      if (-not $body.path) { throw 'path required' }
      $path = [string]$body.path
      $created = $false
      if (-not [System.IO.Directory]::Exists($path)) {
        [System.IO.Directory]::CreateDirectory($path) | Out-Null
        $created = $true
      }
      $out = @{ ok = $true; path = $path; created = $created }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/move' -and $req.HttpMethod -eq 'POST') {
      $reader = New-Object System.IO.StreamReader($req.InputStream, $req.ContentEncoding)
      $raw = $reader.ReadToEnd()
      $body = $raw | ConvertFrom-Json
      if ($body.source) { $src = [string]$body.source }
      elseif ($body.path) { $src = [string]$body.path }
      else { $src = '' }
      if ($body.dest) { $dest = [string]$body.dest }
      elseif ($body.destination) { $dest = [string]$body.destination }
      else { $dest = '' }
      if (-not $src -or -not $dest) { throw 'source and dest required' }
      if (-not (Test-Path -LiteralPath $src)) { throw "not found: $src" }
      $destDir = if ([System.IO.Directory]::Exists($dest)) { $dest } else { [System.IO.Path]::GetDirectoryName($dest) }
      if ($destDir -and -not [System.IO.Directory]::Exists($destDir)) {
        [System.IO.Directory]::CreateDirectory($destDir) | Out-Null
      }
      $moved = Move-Item -LiteralPath $src -Destination $dest -Force -PassThru
      $finalDest = if ($moved -is [Array]) { $moved[0].FullName } else { $moved.FullName }
      $out = @{ ok = $true; source = $src; dest = $finalDest }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/copy' -and $req.HttpMethod -eq 'POST') {
      $reader = New-Object System.IO.StreamReader($req.InputStream, $req.ContentEncoding)
      $raw = $reader.ReadToEnd()
      $body = $raw | ConvertFrom-Json
      if ($body.source) { $src = [string]$body.source }
      elseif ($body.path) { $src = [string]$body.path }
      else { $src = '' }
      if ($body.dest) { $dest = [string]$body.dest }
      elseif ($body.destination) { $dest = [string]$body.destination }
      else { $dest = '' }
      if (-not $src -or -not $dest) { throw 'source and dest required' }
      if (-not (Test-Path -LiteralPath $src)) { throw "not found: $src" }
      $destDir = if ([System.IO.Directory]::Exists($dest)) { $dest } else { [System.IO.Path]::GetDirectoryName($dest) }
      if ($destDir -and -not [System.IO.Directory]::Exists($destDir)) {
        [System.IO.Directory]::CreateDirectory($destDir) | Out-Null
      }
      $isDir = [System.IO.Directory]::Exists($src)
      $copied = Copy-Item -LiteralPath $src -Destination $dest -Recurse:$isDir -Force -PassThru
      $destPath = if ($copied -is [Array]) { $copied[0].FullName } else { $copied.FullName }
      $out = @{ ok = $true; source = $src; dest = $destPath }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/fs/delete' -and $req.HttpMethod -eq 'POST') {
      $reader = New-Object System.IO.StreamReader($req.InputStream, $req.ContentEncoding)
      $raw = $reader.ReadToEnd()
      $body = $raw | ConvertFrom-Json
      $path = [string]$body.path
      if (-not $path) { throw 'path required' }
      if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
      $isDir = [System.IO.Directory]::Exists($path)
      Remove-Item -LiteralPath $path -Recurse:$isDir -Force
      $out = @{ ok = $true; path = $path; type = if ($isDir) { 'dir' } else { 'file' } }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/run' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      $body = $raw | ConvertFrom-Json
      $command = if ($body.command) { [string]$body.command } else { '' }
      if (-not $command.Trim()) { throw 'command required' }
      $cwd = if ($body.cwd) { [string]$body.cwd } else { '' }
      $timeoutSec = 60
      if ($null -ne $body.timeout_sec) { $timeoutSec = [int]$body.timeout_sec }
      if ($timeoutSec -lt 5) { $timeoutSec = 5 }
      if ($timeoutSec -gt 180) { $timeoutSec = 180 }

      # Irreversible / destructive gate — refuse; do not interpret further
      $dangerPatterns = @(
        '(?i)\bRemove-Item\b.*(-Recurse|-r\b)',
        '(?i)\brm\s+(-[a-zA-Z]*f|[a-zA-Z]*rf|-[a-zA-Z]*r\s+-[a-zA-Z]*f)',
        '(?i)\bdel\s+/[sS]',
        '(?i)\brd\s+/[sS]',
        '(?i)\brmdir\s+/[sS]',
        '(?i)\bgit\s+push\b.*(--force|-f\b)',
        '(?i)\bgit\s+reset\s+--hard\b',
        '(?i)\bgit\s+clean\s+-[a-zA-Z]*f',
        '(?i)\bFormat-Volume\b',
        '(?i)\bClear-Disk\b',
        '(?i)\bdiskpart\b',
        '(?i)\bDROP\s+DATABASE\b',
        '(?i)\bInvoke-Expression\b',
        '(?i)\biex\b',
        '(?i)\|\s*iex\b',
        '(?i)curl[^\n]*\|\s*(ba)?sh\b',
        '(?i)iwr[^\n]*\|\s*iex\b'
      )
      $blockedReason = $null
      foreach ($pat in $dangerPatterns) {
        if ($command -match $pat) {
          $blockedReason = 'Refused: command looks irreversible/destructive. Ask the user to run it themselves, or use a safer alternative (git status, tests, build).'
          break
        }
      }

      if ($blockedReason) {
        $blocked = @{
          ok = $false
          blocked = $true
          exit_code = -1
          stdout = ''
          stderr = $blockedReason
          command = $command
        }
        $json = ConvertTo-Json $blocked -Compress
        $buf = [Text.Encoding]::UTF8.GetBytes($json)
        $res.ContentType = 'application/json'
        $res.OutputStream.Write($buf, 0, $buf.Length)
      } else {
        if ($cwd -and -not (Test-Path -LiteralPath $cwd -PathType Container)) {
          throw "cwd not found: $cwd"
        }

        try {
          $psi = New-Object System.Diagnostics.ProcessStartInfo
          $psi.FileName = 'powershell.exe'
          # EncodedCommand avoids PowerShell quote/escaping breakage for pytest, python -c, paths with spaces
          $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
          $psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $enc"
          $psi.UseShellExecute = $false
          $psi.CreateNoWindow = $true
          $psi.RedirectStandardOutput = $true
          $psi.RedirectStandardError = $true
          if ($cwd) { $psi.WorkingDirectory = $cwd }
          $proc = New-Object System.Diagnostics.Process
          $proc.StartInfo = $psi
          [void]$proc.Start()
          $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
          $stderrTask = $proc.StandardError.ReadToEndAsync()
          $timedOut = $false
          if (-not $proc.WaitForExit($timeoutSec * 1000)) {
            $timedOut = $true
            Stop-ProcessTree -ProcessId $proc.Id
            try { [void]$proc.WaitForExit(3000) } catch {}
          }
          if ($timedOut) {
            $stdout = ''
            $stderr = "Timeout after ${timeoutSec}s - process killed."
          } else {
            try { $stdout = $stdoutTask.GetAwaiter().GetResult() } catch { $stdout = '' }
            try { $stderr = $stderrTask.GetAwaiter().GetResult() } catch { $stderr = '' }
          }
          if ($null -eq $stdout) { $stdout = '' }
          if ($null -eq $stderr) { $stderr = '' }
          $combined = $stdout + $stderr
          if ($combined.Length -gt 8192) {
            $head = [Math]::Min(4096, $stdout.Length)
            $tailBudget = 8192 - $head - 32
            if ($tailBudget -lt 0) { $tailBudget = 0 }
            $stdout = $stdout.Substring(0, $head)
            if ($stderr.Length -gt $tailBudget) { $stderr = $stderr.Substring(0, $tailBudget) + "`n...(truncated)" }
            else { $stderr = $stderr + "`n...(truncated)" }
          }
          $exitCode = if ($timedOut) { -1 } else { [int]$proc.ExitCode }
          if ($timedOut) {
            if ($stderr) { $stderr = $stderr + "`n" }
            $stderr = $stderr + "Timeout after ${timeoutSec}s - process killed."
          }
          $out = @{
            ok = (-not $timedOut) -and ($exitCode -eq 0)
            exit_code = $exitCode
            stdout = $stdout
            stderr = $stderr
            command = $command
            cwd = if ($cwd) { $cwd } else { $null }
            timeout_sec = $timeoutSec
            timed_out = $timedOut
          }
          $json = ConvertTo-Json $out -Compress -Depth 4
          $buf = [Text.Encoding]::UTF8.GetBytes($json)
          $res.ContentType = 'application/json'
          $res.OutputStream.Write($buf, 0, $buf.Length)
        } catch {
          throw
        }
      }
    } elseif ($local -eq '/api/web/search') {
      $query = [System.Web.HttpUtility]::UrlDecode($req.QueryString['q'])
      if (-not $query) { throw 'query required' }
      $body = (@{ query = $query; max_results = 8 } | ConvertTo-Json -Compress)
      $search = Invoke-SearchBridge $body
      if (-not $search.ok) {
        $msg = if ($search.error) { $search.error } else { 'search failed' }
        $out = @{ query = $query; results = "Search error: $msg"; source = 'duckduckgo'; ok = $false }
      } else {
        $text = [string]$search.results
        if (-not $text) { $text = 'No results found.' }
        if ($text.Length -gt 12000) { $text = $text.Substring(0, 12000) + "`n...(truncated)" }
        $out = @{ query = $query; results = $text; source = $search.source; ok = $true; count = $search.count }
      }
      $json = ConvertTo-Json $out -Compress
      $buf = [Text.Encoding]::UTF8.GetBytes($json)
      $res.ContentType = 'application/json'
      $res.OutputStream.Write($buf, 0, $buf.Length)
    } elseif ($local -eq '/api/since/status') {
      Write-Json $res (Invoke-SinceBridge 'status' $null)
    } elseif ($local -eq '/api/since/session') {
      $sid = $req.QueryString['sid']
      $body = (@{ session_id = $sid } | ConvertTo-Json -Compress)
      Write-Json $res (Invoke-SinceBridge 'session' $body)
    } elseif ($local -eq '/api/since/staleness') {
      $sid = [System.Web.HttpUtility]::UrlDecode($req.QueryString['sid'])
      $fpath = [System.Web.HttpUtility]::UrlDecode($req.QueryString['path'])
      $body = (@{ session_id = $sid; path = $fpath } | ConvertTo-Json -Compress)
      Write-Json $res (Invoke-SinceBridge 'staleness' $body)
    } elseif ($local -eq '/api/since/record' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      Write-Json $res (Invoke-SinceBridge 'record' $raw)
    } elseif ($local -eq '/api/since/context' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      Write-Json $res (Invoke-SinceBridge 'context' $raw)
    } elseif ($local -eq '/api/since/stamp' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      Write-Json $res (Invoke-SinceBridge 'stamp' $raw)
    } elseif ($local -eq '/api/since/memory') {
      if ($req.HttpMethod -eq 'GET') {
        $nowMs = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        $tz = $req.QueryString['timezone']
        $asOf = $req.QueryString['as_of_ms']
        $body = @{ now_ms = $nowMs }
        if ($tz) { $body['timezone'] = $tz }
        if ($asOf) { $body['as_of_ms'] = [int64]$asOf }
        Write-Json $res (Invoke-SinceBridge 'memory_list' ($body | ConvertTo-Json -Compress))
      } elseif ($req.HttpMethod -eq 'POST') {
        $raw = Read-RequestBodyUtf8 $req
        Write-Json $res (Invoke-SinceBridge 'memory_pin' $raw)
      } elseif ($req.HttpMethod -eq 'DELETE') {
        $raw = Read-RequestBodyUtf8 $req
        Write-Json $res (Invoke-SinceBridge 'memory_forget' $raw)
      }
    } elseif ($local -eq '/api/since/memory/sync' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      Write-Json $res (Invoke-SinceBridge 'memory_sync' $raw)
    } elseif ($local -eq '/api/since/memory/provenance' -and $req.HttpMethod -eq 'GET') {
      $turnId = $req.QueryString['turn_id']
      $nowMs = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
      $body = (@{ turn_id = $turnId; now_ms = $nowMs } | ConvertTo-Json -Compress)
      Write-Json $res (Invoke-SinceBridge 'memory_provenance' $body)
    } elseif ($local -eq '/api/since/book') {
      if ($req.HttpMethod -eq 'GET') {
        $query = [System.Web.HttpUtility]::UrlDecode($req.QueryString['q'])
        $name = [System.Web.HttpUtility]::UrlDecode($req.QueryString['name'])
        $body = @{}
        if ($query) { $body['query'] = $query }
        if ($name) { $body['book_name'] = $name }
        Write-Json $res (Invoke-SinceBridge 'book_list' ($body | ConvertTo-Json -Compress))
      } elseif ($req.HttpMethod -eq 'POST') {
        $raw = Read-RequestBodyUtf8 $req
        Write-Json $res (Invoke-SinceBridge 'book_store' $raw)
      } elseif ($req.HttpMethod -eq 'PUT') {
        $raw = Read-RequestBodyUtf8 $req
        Write-Json $res (Invoke-SinceBridge 'book_update' $raw)
      } elseif ($req.HttpMethod -eq 'DELETE') {
        $raw = Read-RequestBodyUtf8 $req
        Write-Json $res (Invoke-SinceBridge 'book_forget' $raw)
      }
    } elseif ($local -eq '/api/since/book/create' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      Write-Json $res (Invoke-SinceBridge 'book_create' $raw)
    } elseif ($local -eq '/api/diagram/export' -and $req.HttpMethod -eq 'POST') {
      $raw = Read-RequestBodyUtf8 $req
      Write-Json $res (Invoke-SinceBridge 'diagram_export' $raw)
    } else {
      $file = if ($local -eq '/') { 'index.html' } else { $local.TrimStart('/') }
      $path = Join-Path $root $file
      if (Test-Path -LiteralPath $path -PathType Leaf) {
        $bytes = [IO.File]::ReadAllBytes($path)
        $ext = [IO.Path]::GetExtension($path)
        $map = @{'.html'='text/html';'.js'='application/javascript';'.css'='text/css';'.json'='application/json';'.png'='image/png';'.svg'='image/svg+xml';'.webp'='image/webp'}
        $res.ContentType = if ($map.ContainsKey($ext)) { $map[$ext] } else { 'application/octet-stream' }
        $res.ContentLength64 = $bytes.Length
        $res.OutputStream.Write($bytes, 0, $bytes.Length)
      } else { $res.StatusCode = 404; $buf = [Text.Encoding]::UTF8.GetBytes('Not found'); $res.OutputStream.Write($buf, 0, $buf.Length) }
    }
  } catch {
    $res.StatusCode = 400
    $err = "{""error"":""$($_.Exception.Message.Replace('"','\"'))""}"
    $buf = [Text.Encoding]::UTF8.GetBytes($err)
    $res.ContentType = 'application/json'
    $res.OutputStream.Write($buf, 0, $buf.Length)
  }
  $res.Close()
}

$handlerFunctions = @(
  'Read-RequestBodyUtf8',
  'Write-BridgeStdin',
  'Invoke-PythonBridge',
  'Invoke-SinceBridge',
  'Invoke-SearchBridge',
  'Write-Json',
  'Stop-ProcessTree',
  'Invoke-MinimaRequest'
)
$iss = [System.Management.Automation.Runspaces.InitialSessionState]::CreateDefault()
foreach ($fn in $handlerFunctions) {
  $cmd = Get-Command -Name $fn -CommandType Function -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.ScriptBlock) {
    $entry = New-Object System.Management.Automation.Runspaces.SessionStateFunctionEntry($fn, $cmd.ScriptBlock)
    [void]$iss.Commands.Add($entry)
  }
}
[void]$iss.Variables.Add((New-Object System.Management.Automation.Runspaces.SessionStateVariableEntry('root', $root, $null)))
[void]$iss.Variables.Add((New-Object System.Management.Automation.Runspaces.SessionStateVariableEntry('Utf8NoBom', $Utf8NoBom, $null)))

$rsPool = [runspacefactory]::CreateRunspacePool(2, 32, $iss, $Host)
$rsPool.Open()

while ($listener.IsListening) {
  $context = $listener.GetContext()
  $powershell = [powershell]::Create()
  $powershell.RunspacePool = $rsPool
  [void]$powershell.AddScript({
    Param($Context)
    Invoke-MinimaRequest -Context $Context
  }).AddArgument($context)
  $powershell.BeginInvoke() | Out-Null
}
