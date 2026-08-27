import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, 'scripts')
import political_intelligence as pi

events = [
    {"Fase": "entrada", "DataFase": "2026-02-10"},
    {"Fase": "apreciação parlamentar", "DataFase": "2026-03-01"},
    {"Fase": "votação final global", "DataFase": "2026-04-05"},
    {"Fase": "promulgação", "DataFase": "2026-05-02"},
]
print(pi.presidential_action_from_events(events))
