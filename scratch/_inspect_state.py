import io, sys, json
from pathlib import Path
from urllib.request import Request, urlopen

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

try:
    import political_intelligence as pi  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - fallback for IDE/static analysis
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'political_intelligence',
        ROOT / 'scripts' / 'political_intelligence.py',
    )
    if spec is None or spec.loader is None:
        raise
    module = importlib.util.module_from_spec(spec)
    sys.modules['political_intelligence'] = module
    spec.loader.exec_module(module)
    pi = module

cfg = json.load(open('data/political_intelligence_config.json', encoding='utf-8'))
print('gov 2025:', pi.government_label_for_year(2025, cfg['governmentPeriods']))
print('gov 1999:', pi.government_label_for_year(1999, cfg['governmentPeriods']))

dec = json.load(open('scratch/fixtures/ep_dec_event.json', encoding='utf-8'))['data'][0]
rec = pi.normalise_eu_decision(dec, 'https://data.europarl.europa.eu/api/v2', 10)
print('EU decision ->')
for k, v in rec.items():
    s = json.dumps(v, ensure_ascii=False)
    print(f'  {k}: {s[:140]}')

proc = json.load(open('scratch/fixtures/ep_procedure.json', encoding='utf-8'))
state = {'euInitiatives': {}, 'budgetDocuments': {}}
ups, created = pi.apply_eu_procedure_detail(state, proc, 10)
print('apply_eu_procedure_detail ups/created:', ups, created)
st = state['euInitiatives'][list(state['euInitiatives'])[0]]
print('stored title:', st['title'][:80], '| status:', st['status'])

def probe(label, url, headers=None):
    try:
        request = Request(url, headers=headers or {})
        with urlopen(request, timeout=20) as response:
            body = response.read(500).decode('utf-8', errors='replace')
            print(f'{label}: {response.status} {response.headers.get("content-type")} {body!r}')
    except Exception as exc:
        print(f'{label}: {type(exc).__name__}: {exc}')







probe('procs plain', 'https://data.europarl.europa.eu/api/v2/procedures?limit=3&format=application%2Fld%2Bjson')
probe('procs accept-hdr', 'https://data.europarl.europa.eu/api/v2/procedures?limit=3', )
probe('sessions', 'https://data.europarl.europa.eu/api/v2/plenary-sessions?limit=2&format=application%2Fld%2Bjson')

