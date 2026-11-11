import json
from pathlib import Path

root = Path(__file__).resolve().parent / 'workflows'
for mode in ('t2v', 'i2v', 'flf'):
    path = root / f'{mode}_api.json'
    if not path.exists():
        print(f'MISSING: {path.name}')
        continue
    data = json.loads(path.read_text(encoding='utf-8'))
    assert isinstance(data, dict) and data
    assert all(isinstance(n, dict) and 'class_type' in n and 'inputs' in n for n in data.values()), f'{path} is not API JSON'
    text = json.dumps(data)
    assert '__H3_STUDIO_PROMPT__' in text, f'{path}: prompt token missing'
    assert mode == 't2v' or 'STUDIO_FIRST.png' in text, f'{path}: first frame token missing'
    assert mode != 'flf' or 'STUDIO_LAST.png' in text, f'{path}: last frame token missing'
    nodes = [n for n in data.values() if n.get('class_type') == 'SaveVideo']
    assert len(nodes) == 1, f'{path}: expected exactly one SaveVideo node'
    print('OK', path.name, len(data), 'nodes')
