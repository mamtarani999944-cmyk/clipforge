"""
Emoji reactions on every clip card (Dashboard and My Clips), version 2.

Under each clip there are 5 quick emojis and a "+" button that opens a
full emoji picker with ~900 emojis in tabs (faces, hands, hearts, animals,
food, objects, travel). Tap one to mark which emoji fits that clip best.
Tap it again to remove it. The choice is saved to your account.

Works whether or not the first version of this patch was already applied
(it upgrades it).

Run from your clipforge-final project folder:
    python fix_emoji_reactions.py
"""
problems = []
changed = []

# ------------------------------------------------------------ app.py
HELPERS = r'''def _emojiish(ch):
    o = ord(ch)
    return ((0x1F000 <= o <= 0x1FAFF) or (0x2190 <= o <= 0x2BFF)
            or o in (0xA9, 0xAE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299))

def _valid_reaction(e):
    """True for a short string made only of emoji characters (any emoji)."""
    if not isinstance(e, str) or not (1 <= len(e) <= 16):
        return False
    glue = (0x200D, 0xFE0F, 0x20E3)
    if not all(_emojiish(c) or ord(c) in glue for c in e):
        return False
    return any(_emojiish(c) for c in e)

'''

ROUTES = r'''# ── Emoji reactions on clips ─────────────────────────────────────────────────
''' + HELPERS + r'''def _ensure_reactions_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS clip_reactions (
            clip_id INTEGER,
            user_id INTEGER,
            emoji   TEXT NOT NULL,
            PRIMARY KEY (clip_id, user_id)
        )
    """)

@app.route('/api/clips/<int:clip_id>/reaction', methods=['POST'])
@login_required
def set_clip_reaction(clip_id):
    user_id = current_user_id()
    emoji = (request.get_json(silent=True) or {}).get('emoji')
    if not _valid_reaction(emoji):
        return jsonify({'error': 'Unknown emoji'}), 400
    db = get_db()
    _ensure_reactions_table(db)
    clip = db.execute('SELECT id FROM clips WHERE id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if not clip:
        db.close()
        return jsonify({'error': 'Clip not found'}), 404
    current = db.execute('SELECT emoji FROM clip_reactions WHERE clip_id = ? AND user_id = ?', (clip_id, user_id)).fetchone()
    if current and current['emoji'] == emoji:
        db.execute('DELETE FROM clip_reactions WHERE clip_id = ? AND user_id = ?', (clip_id, user_id))
        result = None
    else:
        db.execute("""
            INSERT INTO clip_reactions (clip_id, user_id, emoji) VALUES (?, ?, ?)
            ON CONFLICT(clip_id, user_id) DO UPDATE SET emoji = excluded.emoji
        """, (clip_id, user_id, emoji))
        result = emoji
    db.commit()
    db.close()
    return jsonify({'emoji': result})

'''

with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

if '_valid_reaction' in src:
    print('SKIP: app.py already has the full emoji reactions')
elif 'def set_clip_reaction' in src:
    # Upgrade from version 1 (5 fixed emojis) to version 2 (any emoji).
    old_check = 'if emoji not in REACTION_EMOJIS:'
    marker = '# ── Emoji reactions on clips ─────────────────────────────────────────────────\n'
    if src.count(old_check) == 1 and src.count(marker) == 1:
        src = src.replace(old_check, 'if not _valid_reaction(emoji):', 1)
        src = src.replace(marker, marker + HELPERS, 1)
        with open('app.py', 'w', encoding='utf-8') as f:
            f.write(src)
        changed.append('app.py: any emoji is now accepted (upgraded from version 1)')
    else:
        problems.append('app.py: could not upgrade the earlier reactions code')
else:
    old_rows = """    rows = db.execute('SELECT * FROM clips WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    db.close()"""
    new_rows = """    rows = db.execute('SELECT * FROM clips WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    _ensure_reactions_table(db)
    reactions = {r['clip_id']: r['emoji'] for r in db.execute(
        'SELECT clip_id, emoji FROM clip_reactions WHERE user_id = ?', (user_id,)).fetchall()}
    db.close()"""
    old_field = "            'virality_reason': row['virality_reason'],\n"
    new_field = old_field + "            'reaction': reactions.get(row['id']),\n"
    anchor = "if __name__ == '__main__':"
    for name, old in (('api_clips query', old_rows), ('api_clips fields', old_field), ('main block', anchor)):
        if src.count(old) != 1:
            problems.append(f'app.py: {name} found {src.count(old)} times (expected 1)')
    if not problems:
        src = src.replace(old_rows, new_rows, 1).replace(old_field, new_field, 1).replace(anchor, ROUTES + anchor, 1)
        with open('app.py', 'w', encoding='utf-8') as f:
            f.write(src)
        changed.append('app.py: reactions are saved and returned with each clip')

# --------------------------------------------------------- templates
REACTION_JS = r"""
/* REACTIONS_V2 */
var REACTION_QUICK = ['\u{1F525}', '\u{1F602}', '\u{1F62E}', '❤️', '\u{1F44F}'];
var REACTION_TABS = [
  ['Faces', [[0x1F600,0x1F64F],[0x1F910,0x1F92F],[0x1F970,0x1F97A],[0x1F9D0,0x1F9D0]], ['☺️']],
  ['Hands', [[0x1F440,0x1F450],[0x1F466,0x1F487],[0x1F4AA,0x1F4AA],[0x1F590,0x1F596],[0x1F918,0x1F91F],[0x1F930,0x1F93E],[0x1F9B5,0x1F9B6],[0x1F9D1,0x1F9DF]], ['✌️','☝️','✍️']],
  ['Hearts', [[0x1F493,0x1F49F],[0x1F4A2,0x1F4A9],[0x1F4AB,0x1F4AF],[0x1F500,0x1F53D],[0x1F7E0,0x1F7EB],[0x2600,0x27BF],[0x2B1B,0x2B55]], ['❤️','❣️','✔️','✖️','♻️','✨']],
  ['Animals', [[0x1F400,0x1F43E],[0x1F980,0x1F9AE],[0x1F331,0x1F344],[0x1F308,0x1F30C],[0x1F319,0x1F31F],[0x1F32A,0x1F32C]], ['☀️','☁️']],
  ['Food', [[0x1F345,0x1F37F],[0x1F950,0x1F96F],[0x1F9C0,0x1F9C2]], []],
  ['Objects', [[0x1F380,0x1F3CA],[0x1F3CF,0x1F3D3],[0x1F3E0,0x1F3F0],[0x1F3A0,0x1F3FA],[0x1F4A1,0x1F4A1],[0x1F4B0,0x1F4FC],[0x1F9E0,0x1F9FF]], []],
  ['Travel', [[0x1F680,0x1F6C5],[0x1F30D,0x1F310],[0x1F3D4,0x1F3DF],[0x1F6D0,0x1F6D2],[0x1F6E0,0x1F6EC]], ['✈️']]
];
var _reactionTabCache = null;
function _reactionTabs() {
  if (_reactionTabCache) return _reactionTabCache;
  var seen = {}, out = [];
  var isEmoji = function (s) { try { return /\p{Emoji_Presentation}/u.test(s); } catch (e) { return true; } };
  REACTION_TABS.forEach(function (t) {
    var list = [];
    t[1].forEach(function (r) {
      for (var cp = r[0]; cp <= r[1]; cp++) {
        if (cp >= 0x1F3FB && cp <= 0x1F3FF) continue;
        var s = String.fromCodePoint(cp);
        if (!seen[s] && isEmoji(s)) { seen[s] = 1; list.push(s); }
      }
    });
    (t[2] || []).forEach(function (s) { if (!seen[s]) { seen[s] = 1; list.push(s); } });
    out.push({name: t[0], list: list});
  });
  _reactionTabCache = out;
  return out;
}
function renderReactions(card, clip) {
  if (!clip || !clip.id) return;
  var body = card.querySelector('.clip-body');
  var actions = card.querySelector('.clip-actions');
  if (!body || !actions) return;
  var wrap = document.createElement('div');
  wrap.style.cssText = 'margin:0 0 8px';
  var row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;align-items:center';
  var panel = document.createElement('div');
  panel.style.cssText = 'display:none;margin-top:6px;border:1px solid #e5e5e7;border-radius:10px;background:#fff;padding:6px';
  wrap.appendChild(row);
  wrap.appendChild(panel);
  var panelBuilt = false;

  function save(emoji) {
    return fetch('/api/clips/' + clip.id + '/reaction', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({emoji: emoji})
    }).then(function (res) {
      return res.json().then(function (data) {
        if (res.ok) { clip.reaction = data.emoji; draw(); }
      });
    }).catch(function () {});
  }
  function chip(emoji) {
    var b = document.createElement('button');
    b.type = 'button';
    b.textContent = emoji;
    var on = emoji === clip.reaction;
    b.style.cssText = 'border-radius:8px;padding:4px 8px;font-size:15px;cursor:pointer;line-height:1.2;'
      + 'background:' + (on ? '#eef2ff' : '#f5f5f7') + ';'
      + 'border:1.5px solid ' + (on ? '#6366f1' : 'transparent') + ';'
      + 'transform:' + (on ? 'scale(1.12)' : 'none');
    b.onclick = function () { save(emoji); };
    return b;
  }
  function buildPanel() {
    var tabs = _reactionTabs();
    var bar = document.createElement('div');
    bar.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px';
    var grid = document.createElement('div');
    grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:2px;max-height:150px;overflow-y:auto';
    var tabButtons = [];
    function show(idx) {
      grid.textContent = '';
      tabButtons.forEach(function (tb, i) {
        tb.style.background = i === idx ? '#111' : '#f5f5f7';
        tb.style.color = i === idx ? '#fff' : '#444';
      });
      tabs[idx].list.forEach(function (emoji) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = emoji;
        b.style.cssText = 'background:none;border:none;padding:3px;font-size:20px;cursor:pointer;line-height:1.2';
        b.onclick = function () { panel.style.display = 'none'; save(emoji); };
        grid.appendChild(b);
      });
    }
    tabs.forEach(function (t, i) {
      var tb = document.createElement('button');
      tb.type = 'button';
      tb.textContent = t.name;
      tb.style.cssText = 'border:none;border-radius:6px;padding:3px 7px;font-size:11px;cursor:pointer;font-family:inherit';
      tb.onclick = function () { show(i); };
      tabButtons.push(tb);
      bar.appendChild(tb);
    });
    panel.appendChild(bar);
    panel.appendChild(grid);
    show(0);
  }
  function draw() {
    row.textContent = '';
    var list = REACTION_QUICK.slice();
    if (clip.reaction && list.indexOf(clip.reaction) < 0) list.push(clip.reaction);
    list.forEach(function (emoji) { row.appendChild(chip(emoji)); });
    var more = document.createElement('button');
    more.type = 'button';
    more.textContent = '+';
    more.title = 'More emojis';
    more.style.cssText = 'border-radius:8px;padding:4px 10px;font-size:15px;font-weight:700;cursor:pointer;line-height:1.2;background:#f5f5f7;border:1.5px solid transparent;color:#444';
    more.onclick = function () {
      if (panel.style.display === 'none') {
        if (!panelBuilt) { buildPanel(); panelBuilt = true; }
        panel.style.display = 'block';
      } else {
        panel.style.display = 'none';
      }
    };
    row.appendChild(more);
  }
  draw();
  body.insertBefore(wrap, actions);
}
"""

import re

def patch_template(path):
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    if 'REACTIONS_V2' in html:
        print(f'SKIP: {path} already has the full emoji picker')
        return
    if 'function renderReactions(' in html:
        # upgrade version 1 -> version 2: swap the old block for the new one
        start = html.find('\nvar REACTION_EMOJIS = [')
        end_marker = '  body.insertBefore(row, actions);\n}\n'
        end = html.find(end_marker, start)
        if start == -1 or end == -1:
            problems.append(f'{path}: could not upgrade the earlier emoji buttons')
            return
        html = html[:start] + REACTION_JS + html[end + len(end_marker):]
        what = 'upgraded to the full emoji picker'
    else:
        hook_old = '      grid.appendChild(card);\n'
        hook_new = hook_old + '      renderReactions(card, clip);\n'
        if html.count(hook_old) != 1 or '<script>' not in html:
            problems.append(f'{path}: could not find where to add reactions (hook found {html.count(hook_old)} times)')
            return
        html = html.replace(hook_old, hook_new, 1).replace('<script>', '<script>' + REACTION_JS, 1)
        what = 'emoji buttons and full picker added to every clip card'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    changed.append(f'{path}: {what}')

patch_template('templates/index.html')
patch_template('templates/my_clips.html')

for c in changed:
    print('OK:', c)
for p in problems:
    print('FAILED:', p)
