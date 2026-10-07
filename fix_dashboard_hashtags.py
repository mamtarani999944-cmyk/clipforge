"""
Shows each clip's hashtags on the Dashboard clip cards (they were only
shown on the My Clips page before). Click the hashtags to copy them.

Run from your clipforge-final project folder:
    python fix_dashboard_hashtags.py
"""
path = 'templates/index.html'
with open(path, 'r', encoding='utf-8') as f:
    html = f.read()

if 'dash-hashtags' in html:
    print('SKIP: dashboard hashtags already present')
    raise SystemExit

old_markup = """          <div class="clip-caption-text">${clip.caption}</div>
          <div class="virality-row">"""
new_markup = """          <div class="clip-caption-text">${clip.caption}</div>
          ${clip.hashtags && clip.hashtags.length ? `<div class="dash-hashtags" id="dash-hashtags-${clip.id}" style="font-size:0.7rem;color:#6366f1;margin:2px 0 8px;cursor:pointer;line-height:1.4;word-break:break-word"></div>` : ''}
          <div class="virality-row">"""

old_tail = """      grid.appendChild(card);
    });
  }
async function shareClip("""
new_tail = """      grid.appendChild(card);
      if (clip.hashtags && clip.hashtags.length) {
        const htEl = card.querySelector('.dash-hashtags');
        if (htEl) {
          const tags = clip.hashtags.map(h => String(h).startsWith('#') ? String(h) : '#' + h).join(' ');
          htEl.textContent = tags;
          htEl.title = 'Click to copy';
          htEl.onclick = () => {
            navigator.clipboard.writeText(tags).catch(() => {});
            htEl.title = 'Copied!';
          };
        }
      }
    });
  }
async function shareClip("""

problems = []
if html.count(old_markup) != 1:
    problems.append(f'caption block found {html.count(old_markup)} times (expected 1)')
if html.count(old_tail) != 1:
    problems.append(f'end of showResults found {html.count(old_tail)} times (expected 1)')

if problems:
    print('FAILED, nothing was changed:')
    for p in problems:
        print(' -', p)
else:
    html = html.replace(old_markup, new_markup, 1).replace(old_tail, new_tail, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print('OK: hashtags now show on the Dashboard clip cards (click them to copy)')
