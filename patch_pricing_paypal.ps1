$path = "templates/pricing.html"
$text = [System.IO.File]::ReadAllText((Resolve-Path $path), [System.Text.Encoding]::UTF8)

if ($text.Contains("paypalSubscribe")) {
    Write-Host "SKIPPED: PayPal section already present."
} else {
    $block = @'
<div style="margin-top:40px; padding-top:32px; border-top:1px solid #333;">
  <h2 style="text-align:center; color:#fff;">International Customers</h2>
  <p style="text-align:center; color:#999;">Pay with PayPal (USD)</p>
  <div style="display:flex; gap:16px; justify-content:center; flex-wrap:wrap; margin-top:24px;">
    <div style="background:#1a1a1a; border-radius:12px; padding:24px; width:220px; text-align:center;">
      <h3 style="color:#fff;">Basic</h3>
      <p style="font-size:28px; color:#fff; font-weight:bold;">$5<span style="font-size:14px; color:#999;">/mo</span></p>
      <button onclick="paypalSubscribe(''basic'')" style="background:#0070ba; color:#fff; border:none; border-radius:6px; padding:12px 24px; cursor:pointer; width:100%; font-weight:bold;">Subscribe</button>
    </div>
    <div style="background:#1a1a1a; border-radius:12px; padding:24px; width:220px; text-align:center;">
      <h3 style="color:#fff;">Pro</h3>
      <p style="font-size:28px; color:#fff; font-weight:bold;">$12<span style="font-size:14px; color:#999;">/mo</span></p>
      <button onclick="paypalSubscribe(''pro'')" style="background:#0070ba; color:#fff; border:none; border-radius:6px; padding:12px 24px; cursor:pointer; width:100%; font-weight:bold;">Subscribe</button>
    </div>
    <div style="background:#1a1a1a; border-radius:12px; padding:24px; width:220px; text-align:center;">
      <h3 style="color:#fff;">Premium</h3>
      <p style="font-size:28px; color:#fff; font-weight:bold;">$18<span style="font-size:14px; color:#999;">/mo</span></p>
      <button onclick="paypalSubscribe(''premium'')" style="background:#0070ba; color:#fff; border:none; border-radius:6px; padding:12px 24px; cursor:pointer; width:100%; font-weight:bold;">Subscribe</button>
    </div>
  </div>
</div>
<script>
async function paypalSubscribe(planKey) {
  try {
    const resp = await fetch(''/api/paypal-create-subscription'', {
      method: ''POST'',
      headers: {''Content-Type'': ''application/json''},
      body: JSON.stringify({plan_key: planKey})
    });
    const data = await resp.json();
    if (data.approval_url) {
      window.location.href = data.approval_url;
    } else {
      alert(''Failed to start PayPal subscription: '' + (data.error || ''unknown error''));
    }
  } catch (e) {
    alert(''Error: '' + e.message);
  }
}
</script>
'@

    if ($text.Contains("</body>")) {
        $newText = $text.Replace("</body>", "$block`n</body>")
    } else {
        $newText = $text + "`n" + $block
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Resolve-Path $path), $newText, $utf8NoBom)
    Write-Host "OK: PayPal section added to pricing.html"
}

Write-Host ""
Write-Host "Check:"
Select-String -Path $path -Pattern "paypalSubscribe" | Select-Object -First 3
