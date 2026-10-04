"""
YouTube only shows hashtags as clickable blue links when they're in the
TITLE (not just the description). Right now only #Shorts is in the title,
so that's the only one that shows. This adds your clip's own top 2
hashtags into the title too, right before #Shorts.

Run from your clipforge-final project folder:
    python fix_youtube_title_hashtags.py
"""
with open('app.py', 'r', encoding='utf-8') as f:
    src = f.read()

changed = []

old_block = """        title = (clip_row['title'] or clip_row['caption'] or 'New Clip').strip()
        if len(title) > 90:
            title = title[:90].rstrip()
        if '#shorts' not in title.lower():
            title = f"{title} #Shorts\""""
new_block = """        top_hashtags = ' '.join(hashtag_text.split()[:2])
        title = (clip_row['title'] or clip_row['caption'] or 'New Clip').strip()
        suffix = (' ' + top_hashtags if top_hashtags else '') + ' #Shorts'
        max_title_len = 100 - len(suffix)
        if len(title) > max_title_len:
            title = title[:max_title_len].rstrip()
        if '#shorts' not in title.lower():
            title = f"{title}{suffix}\""""

if old_block in src:
    src = src.replace(old_block, new_block, 1)
    changed.append("YouTube title now includes your clip's own top hashtags, not just #Shorts")
elif 'top_hashtags' in src:
    print('SKIP: title hashtag fix already applied')
else:
    print('FAILED: could not find the title-building block in app.py. No changes made.')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src)

for c in changed:
    print('OK:', c)

print('')
print('Note: this only applies to clips you publish to YouTube AFTER this change.')
print("Already-published videos on YouTube won't update automatically -- you can")
print('edit their title directly on YouTube if you want.')
