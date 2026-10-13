"""python -m fxmon refresh [--live]   |   python -m fxmon check"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

from .alerts import Alerter, dispatch, evaluate, load_config
from .log import event, get_logger
from .models import Snapshot
from .providers import FrankfurterProvider, SnapshotProvider, save_snapshot
from .store import Store

log = get_logger()


def merge(old: Snapshot, new: Snapshot) -> Snapshot:
    rates = {d: dict(r) for d, r in old.rates.items()}
    for d, row in new.rates.items():
        rates.setdefault(d, {}).update(row)
    return Snapshot(source=new.source, base=old.base, fetched_at=new.fetched_at, rates=rates)


def refresh(cfg: dict, snap_path: str, provider=None, today: date | None = None) -> Snapshot:
    """Incremental live refresh: fetch from (last stored date - 7d) to today, merge, write the snapshot file."""
    today = today or datetime.now(timezone.utc).date()
    old = SnapshotProvider(snap_path).load()
    provider = provider or FrankfurterProvider(base=cfg.get("base", "EUR"))
    new = provider.history(old.last_date - timedelta(days=7), today, cfg["currencies"])
    merged = merge(old, new)
    save_snapshot(merged, snap_path)
    event(log, "snapshot_updated", days=len(merged.rates), last=str(merged.last_date))
    return merged


def check(cfg: dict, snap: Snapshot, store: Store | None = None, alerter: Alerter | None = None):
    store = store or Store("data/alerts.db")
    store.upsert(snap)
    return dispatch(evaluate(store.wide(snap.base), cfg), store, alerter or Alerter(), datetime.now(timezone.utc).isoformat(timespec="seconds"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fxmon")
    ap.add_argument("cmd", choices=["refresh", "check"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--snapshot", default="data/snapshot.json")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    snap = refresh(cfg, a.snapshot) if a.cmd == "refresh" else SnapshotProvider(a.snapshot).load()
    fresh = check(cfg, snap)
    print(f"{a.cmd}: last rate date {snap.last_date}, {len(fresh)} new alert(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
