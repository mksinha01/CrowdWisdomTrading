import json
from pathlib import Path

sb = json.loads(Path('runs/20260929-1331-8eff/artifacts/storyboard.json').read_text(encoding='utf-8'))
vo = json.loads(Path('runs/20260929-1331-8eff/artifacts/voiceover.json').read_text(encoding='utf-8'))

print("=== SHOTS IN STORYBOARD ===")
for s in sb['shots']:
    sid = s['id']
    st = s['start_s']
    dur = s['duration_s']
    desc = s.get('description', '')[:50]
    print(f"{sid}: start={st:.2f}s, dur={dur:.2f}s, end={st+dur:.2f}s | {desc}")

print("\n=== VOICEOVER SENTENCES ===")
words = vo['words']
cur_sentence = []
s_start = 0.0
for w in words:
    cur_sentence.append(w['word'])
    if any(w['word'].endswith(p) for p in ('.', '?', '!', ';')):
        print(f"{s_start:5.2f}s - {w['end_s']:5.2f}s: {' '.join(cur_sentence)}")
        cur_sentence = []
        s_start = w['end_s']
if cur_sentence:
    print(f"{s_start:5.2f}s - {words[-1]['end_s']:5.2f}s: {' '.join(cur_sentence)}")
