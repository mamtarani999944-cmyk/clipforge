"""
Fixes the 4 pills at the top of the Dashboard (YouTube / TikTok / Instagram / X):
  - Instagram becomes a real Connect button ("Instagram Connected" when linked)
  - TikTok and X pills are hidden (they were only decoration and did nothing)

Needs fix_add_instagram_publish.py to be installed already.
Run from your clipforge-final folder:  python fix_header_pills.py
"""
path = 'templates/index.html'
with open(path, 'r', encoding='utf-8') as f:
    html = f.read()

import re
if 'HEADER_PILLS_V2' in html:
    print('SKIP: header pills already fixed'); raise SystemExit
html = re.sub(r'<script>\n/\* HEADER_PILLS_V1 \*/.*?</script>\n', '', html, flags=re.S)  # remove the older version
if 'youtubeConnectBtn' not in html or 'INSTAGRAM_V1' not in html:
    print('FAILED, nothing changed: run the YouTube and Instagram patches first'); raise SystemExit
if html.count('</body>') != 1:
    print(f'FAILED, nothing changed: </body> found {html.count("</body>")} times (expected 1)'); raise SystemExit

js = r'''<script>
/* HEADER_PILLS_V2 */
(function () {
  try {
    var yt = document.getElementById('youtubeConnectBtn');
    if (!yt || !yt.parentNode) return;
    var kids = Array.prototype.slice.call(yt.parentNode.children);
    kids.forEach(function (el) {
      if (el === yt) return;
      var t = (el.textContent || '').trim().toLowerCase();
      if (t === 'tiktok' || t === 'x') {
        el.style.display = 'none';
      } else if (t === 'instagram') {
        el.style.cursor = 'pointer';
        el.title = 'Connect Instagram to publish Reels';
        el.onclick = function () { window.location.href = '/instagram/connect'; };
        fetch('/api/instagram/status').then(function (r) { return r.json(); }).then(function (st) {
          if (st && st.connected) {
            var nodes = el.querySelectorAll('span');
            var label = nodes.length ? nodes[nodes.length - 1] : el;
            label.textContent = 'Instagram Connected';
            label.style.color = '#0a0';
            el.onclick = function () { alert('Instagram is connected' + (st.username ? ' as @' + st.username : '') + '. Use "Publish to Instagram" on a clip.'); };
          }
        }).catch(function () {});
      }
    });
  } catch (e) { /* never break the page */ }
})();
</script>
'''
html = html.replace('</body>', js + '</body>', 1)
with open(path, 'w', encoding='utf-8') as f:
    f.write(html)
print('OK: TikTok and X pills hidden; Instagram is a real Connect button')
