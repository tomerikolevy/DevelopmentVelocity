"""Demo: serve the dashboard on a copy of data/ with made-up scores (python scripts/demo_server.py .demo/data)."""
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pathlib import Path
from velocity.config import load_config
from velocity.store import Store
from velocity.server import serve
data = Path(sys.argv[1])
store = Store(data)
prs = store.load_prs()
rng = random.Random(4)
for i, (k, p) in enumerate(sorted(prs.items())):
    if p.get("llm_score") is None:
        s = rng.choice([2, 3, 3, 4, 4, 5, 6, 7])
        p.update(llm_score=s, score=s, score_source="llm", rubric_version=1, model="claude-opus-5-5", effort="medium",
                 dimensions={"scope": rng.randint(1,5), "logic_depth": rng.randint(1,5), "novelty": rng.randint(1,5), "risk": rng.randint(1,5)},
                 summary=f"Demo summary for {p['title']}.", rationale="Demo rationale: moderate logic depth.", confidence="medium",
                 label_status="pending")
    if i == 0: p.update(score=8, score_source="manual")
store.save_prs(prs)
# Model/effort changes in the demo go to a copy of config.toml, never the real one.
import shutil
import velocity.server as server_mod
from velocity.config import CONFIG_PATH, set_scoring
demo_cfg = data.parent / "config.toml"
shutil.copy(CONFIG_PATH, demo_cfg)
server_mod.set_scoring = lambda model, effort: set_scoring(model, effort, path=demo_cfg)
cfg = load_config(demo_cfg); serve(cfg, store, 8788, open_browser=False)
