$path = "app.py"
$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines((Resolve-Path $path)))

$idx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Contains("app = Flask(__name__)")) {
        $idx = $i
        break
    }
}

if ($idx -eq -1) {
    Write-Host "FAILED: could not find 'app = Flask(__name__)' line."
} else {
    # Insert ProxyFix import right before the app creation line
    $lines.Insert($idx, "from werkzeug.middleware.proxy_fix import ProxyFix")
    $idx++  # app = Flask line shifted down by 1

    # Insert ProxyFix wrapping + PREFERRED_URL_SCHEME right after app creation
    [string[]]$block = @(
        "app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)",
        "app.config['PREFERRED_URL_SCHEME'] = 'https'"
    )
    $lines.InsertRange($idx + 1, $block)
    Write-Host "OK: added ProxyFix middleware and PREFERRED_URL_SCHEME after app creation."
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines((Resolve-Path $path), $lines, $utf8NoBom)

Write-Host ""
Write-Host "Context:"
Select-String -Path $path -Pattern "ProxyFix|PREFERRED_URL_SCHEME" -Context 0,1

Write-Host ""
python -c "import ast; ast.parse(open('$path', encoding='utf-8').read()); print('SYNTAX OK')"
