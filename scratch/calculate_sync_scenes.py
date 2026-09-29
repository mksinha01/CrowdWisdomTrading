import json

vo = json.loads(open('runs/20260929-1331-8eff/artifacts/voiceover.json', encoding='utf-8').read())
words = vo['words']

def find_word_time(target_phrase):
    target_tokens = target_phrase.lower().split()
    for i in range(len(words) - len(target_tokens) + 1):
        match = True
        for j, tok in enumerate(target_tokens):
            w = words[i+j]['word'].lower().strip('.,!?;:"-')
            if w != tok:
                match = False
                break
        if match:
            return words[i]['start_s'], words[i+len(target_tokens)-1]['end_s']
    return None

phrases = [
    "too many voices",
    "every day",
    "is certain",
    "so which one is right",
    "following one analyst",
    "blind spots",
    "we read all of them",
    "thousands of professional traders",
    "analysed by ai agents",
    "consensus that actually holds",
    "with the entry",
    "the targets and the stops",
    "part nobody else does",
    "every single call is published",
    "the wins and the misses",
    "read the whole record",
    "not a secret",
    "collective intelligence for traders",
]

for p in phrases:
    res = find_word_time(p)
    if res:
        print(f"'{p}': {res[0]:.2f}s - {res[1]:.2f}s")
    else:
        print(f"'{p}': NOT FOUND")
