"""
Part 2 of 2 for AI Voice-Over. Run fix_add_ai_voiceover.py FIRST.

Wires the voiceover_enabled flag from the UI checkbox through the /upload
route into generate_clips(), same pattern as the AI B-roll checkbox.

Run from your clipforge-final project folder, in this order:
    python fix_add_ai_voiceover.py
    python fix_wire_up_voiceover.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

# 1. /upload route: read the new 'voiceover' checkbox field.
old_upload_read = """    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))"""
new_upload_read = """    num_clips = int(request.form.get('num_clips', 3))
    clip_duration = int(request.form.get('clip_duration', 30))
    voiceover_enabled = request.form.get('voiceover', 'false').lower() in ('1', 'true', 'on', 'yes')"""
if old_upload_read in src:
    src = src.replace(old_upload_read, new_upload_read, 1)
    changed.append("added voiceover_enabled = request.form.get('voiceover', ...) in /upload")
elif 'voiceover_enabled = request.form.get(' in src:
    print('SKIP: /upload already reads the voiceover field')
else:
    print('FAILED: could not find the num_clips/clip_duration read in /upload. No changes made there. (Did a different patch already change this block? If so tell me.)')

# 2. _run_job(): pass voiceover_enabled into generate_clips(). Tolerant of
#    the AI B-roll patch having already added broll_enabled to this call.
call_prefix = 'clips = generate_clips(video_path, num_clips=num_clips, clip_duration=clip_duration'
call_idx = src.find(call_prefix)
if call_idx != -1 and 'voiceover_enabled=voiceover_enabled' not in src[call_idx:call_idx + 200]:
    line_end = src.find('\n', call_idx)
    line = src[call_idx:line_end]
    new_line = line.replace(')', ', voiceover_enabled=voiceover_enabled)', 1)
    src = src[:call_idx] + new_line + src[line_end:]
    changed.append('/upload passes voiceover_enabled into generate_clips()')
elif call_idx != -1:
    print('SKIP: generate_clips() call already passes voiceover_enabled')
else:
    print('FAILED: could not find the generate_clips() call inside /upload. No changes made there.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

# 3. templates/index.html: add the checkbox UI and wire it into the upload request.
html_path = 'templates/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

html_changed = []

# Anchor after the AI B-roll checkbox if present, otherwise after the
# duration/settings box -- so this script works whether or not the AI
# B-roll patch ran first.
broll_checkbox_marker = '''      <div class="setting-box" style="margin-top:10px">
        <label class="setting-label" style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="brollToggle" style="width:auto" />
          AI B-Roll &mdash; auto-insert short stock footage over key moments
        </label>
      </div>
'''
plain_settings_marker = '''        <div class="setting-box">
          <label class="setting-label">Duration &mdash; <span id="durVal">30s</span></label>
          <div class="range-row">
            <input type="range" id="clipDur" min="15" max="60" step="5" value="30"
              oninput="document.getElementById('durVal').textContent=this.value+'s'" />
          </div>
        </div>
      </div>
'''

voiceover_checkbox = '''      <div class="setting-box" style="margin-top:10px">
        <label class="setting-label" style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="voiceoverToggle" style="width:auto" />
          AI Voice-Over &mdash; adds a spoken hook narration at the start of each clip
        </label>
      </div>
'''

if 'id="voiceoverToggle"' in html:
    print('SKIP: voiceoverToggle checkbox already present')
elif broll_checkbox_marker in html:
    html = html.replace(broll_checkbox_marker, broll_checkbox_marker + voiceover_checkbox, 1)
    html_changed.append('added the AI Voice-Over checkbox after the AI B-Roll checkbox')
elif plain_settings_marker in html:
    html = html.replace(plain_settings_marker, plain_settings_marker + voiceover_checkbox, 1)
    html_changed.append('added the AI Voice-Over checkbox to the settings panel')
else:
    print('FAILED: could not find a place to add the AI Voice-Over checkbox in index.html. No changes made there.')

broll_form_append = "    form.append('broll', document.getElementById('brollToggle') && document.getElementById('brollToggle').checked ? 'true' : 'false');"
plain_form_build = '''    form.append('num_clips', document.getElementById('numClips').value);
    form.append('clip_duration', document.getElementById('clipDur').value);'''

voiceover_form_append = "    form.append('voiceover', document.getElementById('voiceoverToggle') && document.getElementById('voiceoverToggle').checked ? 'true' : 'false');"

if "form.append('voiceover'" in html:
    print('SKIP: upload JS already sends the voiceover flag')
elif broll_form_append in html:
    html = html.replace(broll_form_append, broll_form_append + '\n' + voiceover_form_append, 1)
    html_changed.append('generateClips() now sends the voiceover flag with the upload request')
elif plain_form_build in html:
    html = html.replace(plain_form_build, plain_form_build + '\n' + voiceover_form_append, 1)
    html_changed.append('generateClips() now sends the voiceover flag with the upload request')
else:
    print('FAILED: could not find the FormData build block in index.html. No changes made there.')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)

for c in html_changed:
    print('OK:', c)

print('')
print('Verify with:')
print('''python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('app.py SYNTAX OK')"''')
print('')
print('AI Voice-Over needs NO API key or signup -- it uses a free built-in voice engine.')
print('Just push to GitHub/Railway like normal and the checkbox will work immediately.')
print('')
print('Honest heads-up: this uses an unofficial free voice service (not an official paid API),')
print('so it could occasionally stop working without warning. If that happens, clips still')
print('generate normally -- the voice-over is just skipped automatically. Tell me if you ever')
print('notice the voice-over stops appearing and I will look into it.')
