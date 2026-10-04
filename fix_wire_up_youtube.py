"""
Part 2 of 2 for YouTube Publishing. Run fix_add_youtube_publish.py FIRST.

Turns the existing (decorative) "YouTube" pill in the dashboard header into
a real "Connect YouTube" / "YouTube Connected" button, and adds a "Publish
to YouTube" button to every generated clip card.

Run from your clipforge-final project folder, in this order:
    python fix_add_youtube_publish.py
    python fix_wire_up_youtube.py
"""
html_path = 'templates/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

changed = []

# 1. Turn the static YouTube pill into a real connect button.
old_pill = '''          <div style="display:flex;align-items:center;gap:7px;background:#fff;border:1px solid #e5e5e7;border-radius:10px;padding:6px 12px"><svg width="15" height="15" viewBox="0 0 24 24"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.95-1.96C18.88 4 12 4 12 4s-6.88 0-8.59.46A2.78 2.78 0 0 0 1.46 6.42 29 29 0 0 0 1 12a29 29 0 0 0 .46 5.58A2.78 2.78 0 0 0 3.41 19.54C5.12 20 12 20 12 20s6.88 0 8.59-.46a2.78 2.78 0 0 0 1.95-1.96A29 29 0 0 0 23 12a29 29 0 0 0-.46-5.58z" fill="#FF0000"/><polygon points="9.75 15.02 15.5 12 9.75 8.98 9.75 15.02" fill="#fff"/></svg><span style="font-size:12px;font-weight:600;color:#111">YouTube</span></div>'''
new_pill = '''          <button id="youtubeConnectBtn" onclick="connectYoutube()" style="display:flex;align-items:center;gap:7px;background:#fff;border:1px solid #e5e5e7;border-radius:10px;padding:6px 12px;cursor:pointer;font-family:inherit"><svg width="15" height="15" viewBox="0 0 24 24"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.95-1.96C18.88 4 12 4 12 4s-6.88 0-8.59.46A2.78 2.78 0 0 0 1.46 6.42 29 29 0 0 0 1 12a29 29 0 0 0 .46 5.58A2.78 2.78 0 0 0 3.41 19.54C5.12 20 12 20 12 20s6.88 0 8.59-.46a2.78 2.78 0 0 0 1.95-1.96A29 29 0 0 0 23 12a29 29 0 0 0-.46-5.58z" fill="#FF0000"/><polygon points="9.75 15.02 15.5 12 9.75 8.98 9.75 15.02" fill="#fff"/></svg><span id="youtubeConnectLabel" style="font-size:12px;font-weight:600;color:#111">Connect YouTube</span></button>'''
if old_pill in html:
    html = html.replace(old_pill, new_pill, 1)
    changed.append('turned the YouTube pill into a real Connect button')
elif 'id="youtubeConnectBtn"' in html:
    print('SKIP: YouTube connect button already present')
else:
    print('FAILED: could not find the YouTube pill markup. No changes made there.')

# 2. Add the "Publish to YouTube" button to each clip card, plus the JS
#    that drives it and the connect-button status check.
old_actions = '''         <div class="clip-actions">
  <a class="clip-btn primary" href="${clip.download_url}" download>&#11015; Download</a>
  <button class="clip-btn" onclick="previewClip('${clip.download_url}')">&#9654; Preview</button>
  <button class="clip-btn" onclick="shareClip('${clip.download_url}', '${clip.caption}')">&#128279; Share</button>
</div>'''
new_actions = '''         <div class="clip-actions">
  <a class="clip-btn primary" href="${clip.download_url}" download>&#11015; Download</a>
  <button class="clip-btn" onclick="previewClip('${clip.download_url}')">&#9654; Preview</button>
  <button class="clip-btn" onclick="shareClip('${clip.download_url}', '${clip.caption}')">&#128279; Share</button>
</div>
<div class="clip-actions" style="margin-top:5px">
  <button class="clip-btn" id="yt-publish-${clip.id}" onclick="publishToYoutube(${clip.id}, this)" style="background:#FF0000;color:#fff">&#9654; Publish to YouTube</button>
</div>'''
if old_actions in html:
    html = html.replace(old_actions, new_actions, 1)
    changed.append('added a Publish to YouTube button to each clip card')
elif 'publishToYoutube(' in html:
    print('SKIP: Publish to YouTube button already present')
else:
    print('FAILED: could not find the clip-actions block to extend. No changes made there.')

youtube_js = '''
  async function checkYoutubeStatus() {
    try {
      const res = await fetch('/api/youtube/status');
      const data = await res.json();
      const label = document.getElementById('youtubeConnectLabel');
      if (!label) return;
      if (data.connected) {
        label.textContent = 'YouTube Connected';
        label.style.color = '#0a0';
      } else if (!data.configured) {
        label.textContent = 'YouTube (not set up)';
      } else {
        label.textContent = 'Connect YouTube';
      }
    } catch (e) { /* ignore */ }
  }
  function connectYoutube() {
    window.location.href = '/youtube/connect';
  }
  async function publishToYoutube(clipId, btn) {
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Publishing...';
    try {
      const res = await fetch(`/api/clips/${clipId}/publish-youtube`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        btn.textContent = 'Published! Open \\u2192';
        btn.onclick = () => window.open(data.youtube_url, '_blank');
        btn.disabled = false;
      } else {
        alert(data.error || 'Publish failed.');
        btn.textContent = originalText;
        btn.disabled = false;
      }
    } catch (e) {
      alert('Publish failed: ' + e.message);
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }
  (function() {
    checkYoutubeStatus();
    const params = new URLSearchParams(window.location.search);
    if (params.get('youtube') === 'connected') {
      alert('YouTube connected!');
      checkYoutubeStatus();
    } else if (params.get('youtube_error')) {
      alert('YouTube connection failed: ' + params.get('youtube_error'));
    }
  })();
'''

if 'function checkYoutubeStatus' in html:
    print('SKIP: YouTube JS already present')
elif '<script>' in html:
    html = html.replace('<script>', '<script>' + youtube_js, 1)
    changed.append('added the YouTube connect/publish JavaScript')
else:
    print('FAILED: could not find a <script> tag to add the YouTube JS into. No changes made there.')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)

for c in changed:
    print('OK:', c)

print('')
print('Needs YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET set in Railway Variables')
print('(use the Client ID / Client Secret from the "ClipForge YouTube" OAuth client you created).')
print('Once deployed: click "Connect YouTube" once, sign in, then each clip gets a')
print('"Publish to YouTube" button.')
