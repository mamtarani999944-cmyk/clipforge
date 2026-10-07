"""
Makes sure every clip card shows ALL its details:

  Dashboard:  title, caption, hashtags (from the earlier fix), the AI's
              reason for the virality score, and the transcript (tap to open).
  My Clips:   adds the reason for the virality score
              (it already had title, caption, hashtags and transcript).

Run from your clipforge-final project folder:
    python fix_show_all_clip_details.py
"""
changed = []
problems = []

# ---------------- Dashboard (templates/index.html) ----------------
p = 'templates/index.html'
with open(p, 'r', encoding='utf-8') as f:
    html = f.read()

if 'dash-reason' in html:
    print('SKIP: dashboard already shows all details')
else:
    old_caption = '          <div class="clip-caption-text">${clip.caption}</div>'
    new_caption = ('          ${clip.title ? `<div class="dash-title" style="font-weight:700;font-size:0.86rem;margin-bottom:3px;line-height:1.3"></div>` : \'\'}\n'
                   + old_caption)
    old_bar = '<div class="virality-bar"><div class="virality-fill" style="width:${score}%"></div></div>'
    new_bar = (old_bar + '\n'
        '          ${clip.virality_reason ? `<div class="dash-reason" style="font-size:0.7rem;color:#777;margin:6px 0 8px;line-height:1.4"></div>` : \'\'}\n'
        '          ${clip.transcript ? `<details style="margin:0 0 8px;font-size:0.72rem"><summary style="cursor:pointer;color:#555">&#128221; Transcript</summary><div class="dash-transcript-text" style="margin-top:4px;color:#444;white-space:pre-wrap;max-height:120px;overflow:auto"></div></details>` : \'\'}')
    old_append = '      grid.appendChild(card);\n'
    new_append = (old_append +
        "      const _t = card.querySelector('.dash-title'); if (_t) _t.textContent = clip.title;\n"
        "      const _r = card.querySelector('.dash-reason'); if (_r) _r.textContent = clip.virality_reason;\n"
        "      const _x = card.querySelector('.dash-transcript-text'); if (_x) _x.textContent = clip.transcript;\n")
    for name, old in (('caption line', old_caption), ('virality bar', old_bar), ('grid.appendChild(card)', old_append)):
        if html.count(old) != 1:
            problems.append(f'dashboard: {name} found {html.count(old)} times (expected 1)')
    if not any(x.startswith('dashboard') for x in problems):
        html = html.replace(old_caption, new_caption, 1).replace(old_bar, new_bar, 1).replace(old_append, new_append, 1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(html)
        changed.append('Dashboard cards now show title, reason for the score, and transcript')

# ---------------- My Clips (templates/my_clips.html) ----------------
p = 'templates/my_clips.html'
with open(p, 'r', encoding='utf-8') as f:
    mc = f.read()

if 'mc-reason' in mc:
    print('SKIP: My Clips already shows the score reason')
else:
    old_bar = '<div class="virality-bar"><div class="virality-fill" style="width:${score}%"></div></div>'
    new_bar = (old_bar + '\n'
        '          ${clip.virality_reason ? `<div class="mc-reason" style="font-size:0.68rem;color:#777;margin:5px 0 8px;line-height:1.4"></div>` : \'\'}')
    old_ta = "      const taEl = card.querySelector('#transcript-text-' + clip.id);"
    new_ta = ("      const _mr = card.querySelector('.mc-reason'); if (_mr) _mr.textContent = clip.virality_reason;\n" + old_ta)
    for name, old in (('virality bar', old_bar), ('transcript textarea line', old_ta)):
        if mc.count(old) != 1:
            problems.append(f'my clips: {name} found {mc.count(old)} times (expected 1)')
    if not any(x.startswith('my clips') for x in problems):
        mc = mc.replace(old_bar, new_bar, 1).replace(old_ta, new_ta, 1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(mc)
        changed.append('My Clips cards now show the reason for the score')

for c in changed:
    print('OK:', c)
for x in problems:
    print('FAILED:', x)
