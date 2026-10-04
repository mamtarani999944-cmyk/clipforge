"""
Adds the "Publish to YouTube" button to the My Clips page too (it was only
added to the Dashboard page before). Needs fix_add_youtube_publish.py and
fix_wire_up_youtube.py to have already been run.

Run from your clipforge-final project folder:
    python fix_myclips_youtube.py
"""
html_path = 'templates/my_clips.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

changed = []

old_actions = '''          <div class="clip-actions">
            ${clip.exists
              ? `<a class="clip-btn primary" href="${clip.download_url}" download>&#11015;</a>`
              : `<span class="clip-btn primary" style="opacity:0.4;cursor:not-allowed">&#11015;</span>`}
            <button class="clip-btn" onclick="previewClip('${clip.download_url}')" ${!clip.exists ? 'disabled style="opacity:0.4"' : ''}>&#9654;</button>
            <button class="clip-btn" onclick="toggleTranscript(${clip.id})">&#128221;</button>
            <button class="clip-btn danger" onclick="deleteClip(${clip.id}, this)">&#128465;</button>
          </div>'''
new_actions = '''          <div class="clip-actions">
            ${clip.exists
              ? `<a class="clip-btn primary" href="${clip.download_url}" download>&#11015;</a>`
              : `<span class="clip-btn primary" style="opacity:0.4;cursor:not-allowed">&#11015;</span>`}
            <button class="clip-btn" onclick="previewClip('${clip.download_url}')" ${!clip.exists ? 'disabled style="opacity:0.4"' : ''}>&#9654;</button>
            <button class="clip-btn" onclick="toggleTranscript(${clip.id})">&#128221;</button>
            <button class="clip-btn danger" onclick="deleteClip(${clip.id}, this)">&#128465;</button>
          </div>
          <div class="clip-actions" style="margin-top:5px">
            <button class="clip-btn" id="yt-publish-${clip.id}" onclick="publishToYoutube(${clip.id}, this)" style="background:#FF0000;color:#fff" ${!clip.exists ? 'disabled style="opacity:0.4"' : ''}>&#9654; Publish to YouTube</button>
          </div>'''
if old_actions in html:
    html = html.replace(old_actions, new_actions, 1)
    changed.append('added a Publish to YouTube button to each clip card on My Clips')
elif 'publishToYoutube(' in html:
    print('SKIP: Publish to YouTube button already present on My Clips')
else:
    print('FAILED: could not find the clip-actions block in my_clips.html. No changes made there.')

youtube_js = '''
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
        alert(data.error || 'Publish failed. Did you connect YouTube from the Dashboard page first?');
        btn.textContent = originalText;
        btn.disabled = false;
      }
    } catch (e) {
      alert('Publish failed: ' + e.message);
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }
'''

if 'function publishToYoutube' in html:
    print('SKIP: YouTube publish JS already present on My Clips')
elif '<script>' in html:
    html = html.replace('<script>', '<script>' + youtube_js, 1)
    changed.append('added the YouTube publish JavaScript to My Clips')
else:
    print('FAILED: could not find a <script> tag in my_clips.html. No changes made there.')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)

for c in changed:
    print('OK:', c)

print('')
print('Done. Each saved clip on the My Clips page now has a Publish to YouTube button too.')
