"""One-off bootstrap of data/snapshot.json (~1 year of real ECB reference rates via Frankfurter)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fxmon.alerts import load_config  # noqa: E402
from fxmon.providers import FrankfurterProvider, save_snapshot  # noqa: E402

cfg = load_config()
today = date.today()
snap = FrankfurterProvider(base=cfg["base"]).history(today - timedelta(days=400), today, cfg["currencies"])
save_snapshot(snap, "data/snapshot.json")
print(len(snap.rates), "days", snap.last_date)
