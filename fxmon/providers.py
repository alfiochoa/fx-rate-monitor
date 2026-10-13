"""Provider/adapter layer: live Frankfurter API or a bundled snapshot (offline)."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

import requests

from . import USER_AGENT
from .log import event, get_logger
from .models import FrankfurterRange, Snapshot

log = get_logger()
API = "https://api.frankfurter.dev/v1"
RETRY = {429, 500, 502, 503, 504}


class Provider(Protocol):
    name: str

    def history(self, start: date, end: date, symbols: list[str]) -> Snapshot: ...


class ProviderError(Exception):
    pass


class FrankfurterProvider:
    """ECB reference rates. Free, no key. Timeouts, retries with backoff, min request interval, on-disk cache."""
    name = "Frankfurter (ECB reference rates)"

    def __init__(self, base="EUR", session=None, cache_dir: str | None = ".cache", cache_ttl=3600, retries=3,
                 backoff=1.0, min_interval=0.5, sleep=time.sleep, timeout=15):
        self.base, self.retries, self.backoff, self.min_interval, self.timeout = base, retries, backoff, min_interval, timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.cache_dir, self.cache_ttl, self.sleep, self._last = (Path(cache_dir) if cache_dir else None), cache_ttl, sleep, 0.0

    def _get(self, url: str, params: dict) -> dict:
        key = hashlib.sha256((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
        cp = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cp and cp.exists() and time.time() - cp.stat().st_mtime < self.cache_ttl:
            event(log, "cache_hit", url=url)
            return json.loads(cp.read_text())
        err = None
        for attempt in range(self.retries + 1):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code in RETRY:
                    err = f"HTTP {r.status_code}"
                else:
                    r.raise_for_status()
                    data = r.json()
                    if cp:
                        cp.parent.mkdir(parents=True, exist_ok=True)
                        cp.write_text(json.dumps(data))
                    return data
            except requests.HTTPError as e:
                raise ProviderError(str(e)) from e
            except (requests.RequestException, ValueError) as e:
                err = f"{type(e).__name__}: {e}"
            event(log, "retry", attempt=attempt + 1, error=err)
            if attempt < self.retries:
                self.sleep(self.backoff * 2**attempt)
        raise ProviderError(f"giving up on {url}: {err}")

    def history(self, start: date, end: date, symbols: list[str]) -> Snapshot:
        data = self._get(f"{API}/{start}..{end}", {"base": self.base, "symbols": ",".join(symbols)})
        parsed = FrankfurterRange.model_validate(data)  # schema validation
        event(log, "fetched", days=len(parsed.rates), start=str(start), end=str(end))
        return Snapshot(source=self.name, base=parsed.base, fetched_at=datetime.now(timezone.utc), rates=parsed.rates)


class SnapshotProvider:
    """Reads a bundled JSON snapshot: tests, CI and the Streamlit demo work fully offline."""
    name = "Bundled snapshot"

    def __init__(self, path: str | Path = "data/snapshot.json"):
        self.path = Path(path)

    def load(self) -> Snapshot:
        return Snapshot.model_validate_json(self.path.read_text())

    def history(self, start: date, end: date, symbols: list[str]) -> Snapshot:
        s = self.load()
        rates = {d: {c: r for c, r in row.items() if c in symbols} for d, row in s.rates.items() if start <= d <= end}
        return Snapshot(source=s.source, base=s.base, fetched_at=s.fetched_at, rates=rates)


def save_snapshot(s: Snapshot, path: str | Path) -> None:
    out = {"source": s.source, "base": s.base, "fetched_at": s.fetched_at.isoformat(timespec="seconds"),
           "rates": {str(d): dict(sorted(row.items())) for d, row in sorted(s.rates.items())}}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(out, separators=(",", ":")))
