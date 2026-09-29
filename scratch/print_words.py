import json
vo = json.loads(open('runs/20260929-1331-8eff/artifacts/voiceover.json', encoding='utf-8').read())
for i, w in enumerate(vo['words']):
    print(f"{w['start_s']:5.2f} - {w['end_s']:5.2f}: {w['word']}")
