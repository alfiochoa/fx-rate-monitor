import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests
from pydantic import ValidationError

from fxmon.alerts import Alerter, dispatch, evaluate, load_config
from fxmon.analytics import n_day_extreme, pair, rolling_vol, zscore_moves
from fxmon.cli import check, merge, refresh
from fxmon.convert import invoice_value, scenarios
from fxmon.models import FrankfurterRange, Snapshot
from fxmon.providers import FrankfurterProvider, ProviderError, SnapshotProvider
from fxmon.store import Store

ROOT = Path(__file__).parent.parent
SNAP = ROOT / "data" / "snapshot.json"


def snap(series: dict[str, list[float]], start="2026-01-01") -> Snapshot:
    idx = pd.bdate_range(start, periods=len(next(iter(series.values()))))
    rates = {d.date(): {c: v[i] for c, v in series.items()} for i, d in enumerate(idx)}
    return Snapshot(source="test", base="EUR", fetched_at=datetime.now(timezone.utc), rates=rates)


def wide_of(s: Snapshot) -> pd.DataFrame:
    st = Store()
    st.upsert(s)
    return st.wide("EUR")


# ---------- schema ----------
def test_bundled_snapshot_is_valid_and_fresh_enough():
    s = SnapshotProvider(SNAP).load()
    assert s.base == "EUR" and "USD" in s.currencies and len(s.rates) > 200


def test_schema_rejects_bad_rates():
    with pytest.raises(ValidationError):
        Snapshot(source="x", base="EUR", fetched_at=datetime.now(timezone.utc), rates={date(2026, 1, 1): {"USD": -1.0}})
    with pytest.raises(ValidationError):
        FrankfurterRange.model_validate({"base": "EUR", "rates": {}})


# ---------- provider resilience ----------
class Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b = code, body

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Sess:
    def __init__(self, seq):
        self.seq, self.headers, self.calls = list(seq), {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        r = self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]
        if isinstance(r, Exception):
            raise r
        return r


OK = {"base": "EUR", "start_date": "2026-01-01", "end_date": "2026-01-02", "rates": {"2026-01-01": {"USD": 1.1}, "2026-01-02": {"USD": 1.2}}}


def prov(seq, **kw):
    sl = []
    p = FrankfurterProvider(session=Sess(seq), cache_dir=None, sleep=sl.append, min_interval=0, **kw)
    return p, sl


def test_retries_with_backoff_then_succeeds_and_sets_ua():
    p, sleeps = prov([Resp(503), requests.ConnectionError("x"), Resp(200, OK)])
    s = p.history(date(2026, 1, 1), date(2026, 1, 2), ["USD"])
    assert len(s.rates) == 2 and sleeps == [1.0, 2.0]
    assert "fx-rate-monitor" in p.session.headers["User-Agent"] and p.session.calls[0][2] == 15


def test_gives_up_and_does_not_retry_4xx():
    p, _ = prov([Resp(500)], retries=1)
    with pytest.raises(ProviderError):
        p.history(date(2026, 1, 1), date(2026, 1, 2), ["USD"])
    p, _ = prov([Resp(404)])
    with pytest.raises(ProviderError):
        p.history(date(2026, 1, 1), date(2026, 1, 2), ["USD"])
    assert len(p.session.calls) == 1


def test_malformed_payload_is_rejected():
    p, _ = prov([Resp(200, {"base": "EUR", "rates": "nope"})])
    with pytest.raises(ValidationError):
        p.history(date(2026, 1, 1), date(2026, 1, 2), ["USD"])


def test_cache_avoids_second_request(tmp_path):
    p = FrankfurterProvider(session=Sess([Resp(200, OK)]), cache_dir=str(tmp_path), sleep=lambda s: None, min_interval=0)
    p.history(date(2026, 1, 1), date(2026, 1, 2), ["USD"])
    p.history(date(2026, 1, 1), date(2026, 1, 2), ["USD"])
    assert len(p.session.calls) == 1


# ---------- analytics ----------
def test_cross_rate_via_eur_legs():
    w = wide_of(snap({"USD": [1.10, 1.10], "GBP": [0.85, 0.85]}))
    assert pair(w, "USD", "GBP").iloc[-1] == pytest.approx(0.85 / 1.10)
    assert pair(w, "EUR", "USD").iloc[-1] == 1.10


def test_zscore_flags_the_shock_without_lookahead():
    rng = np.random.default_rng(1)
    vals = list(1.1 * np.cumprod(1 + rng.normal(0, 0.003, 80)))
    vals.append(vals[-1] * 1.03)  # 3% jump after ~0.3% daily noise
    s = pair(wide_of(snap({"USD": vals})), "EUR", "USD")
    z = zscore_moves(s)
    assert z.iloc[-1] > 5 and z.iloc[40:-1].abs().max() < 3.5


def test_rolling_vol_known_value():
    s = pd.Series(1.0 * np.cumprod(np.where(np.arange(60) % 2, 1.01, 1 / 1.01)))
    assert rolling_vol(s, 20).iloc[-1] == pytest.approx(np.log(1.01) * np.sqrt(252) * 100 * np.sqrt(20 / 19) * 1, rel=0.05)


def test_n_day_extreme():
    assert n_day_extreme(pd.Series(list(range(40)) + [100]), 30) == "high"
    assert n_day_extreme(pd.Series(list(range(40, 0, -1)) + [-5]), 30) == "low"
    assert n_day_extreme(pd.Series([1, 2, 1.5] * 20), 30) is None


# ---------- alerts ----------
CFG = {"base": "EUR", "pairs": ["EUR/USD"], "thresholds": [{"pair": "EUR/USD", "above": 1.15}], "nday": 30, "zscore": 2.5}


def test_threshold_cross_fires_once_and_dedupes():
    vals = [1.10] * 59 + [1.11, 1.16]
    w = wide_of(snap({"USD": vals}))
    kinds = [a.kind for a in evaluate(w, CFG)]
    assert "threshold" in kinds and "30d_high" in kinds
    st, al = Store(), Alerter(url="")
    assert len(dispatch(evaluate(w, CFG), st, al, "t")) >= 2
    assert dispatch(evaluate(w, CFG), st, al, "t") == []  # same day -> no repeat
    assert all("not advice" in m for m in al.sent)


def test_no_alert_when_nothing_happens():
    w = wide_of(snap({"USD": list(1.10 + 0.002 * np.sin(np.arange(80)))}))
    assert [a for a in evaluate(w, {**CFG, "thresholds": []}) if a.kind == "threshold"] == []


def test_webhook_payloads(monkeypatch):
    posted = []
    monkeypatch.setattr("fxmon.alerts.requests.post", lambda u, json, timeout: posted.append(json) or type("R", (), {"raise_for_status": lambda s: None})())
    Alerter(url="http://h", kind="slack").send("a")
    Alerter(url="http://h", kind="discord").send("b")
    assert posted == [{"text": "a"}, {"content": "b"}]


# ---------- converter ----------
def test_invoice_value_and_scenarios():
    w = wide_of(snap({"USD": [1.25] * 100}))
    val, rate = invoice_value(w, 1000, "USD", "EUR")
    assert (val, rate) == (pytest.approx(800.0), pytest.approx(0.8))
    sc = scenarios(w, 1000, "USD", "EUR")
    assert sc.loc[sc.scenario == "rate +0%", "value"].iloc[0] == pytest.approx(800.0)
    assert sc.loc[sc.scenario == "rate -5%", "vs today"].iloc[0] == pytest.approx(-40.0)
    assert sc.scenario.str.startswith("historical").sum() == 3


# ---------- refresh / cli ----------
def test_refresh_merges_incrementally(tmp_path):
    base = snap({"USD": [1.10, 1.11, 1.12]}, "2026-01-05")
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"source": "t", "base": "EUR", "fetched_at": "2026-01-07T00:00:00+00:00",
                                "rates": {str(d): r for d, r in base.rates.items()}}))
    newer = Snapshot(source="live", base="EUR", fetched_at=datetime.now(timezone.utc), rates={date(2026, 1, 7): {"USD": 1.13}, date(2026, 1, 8): {"USD": 1.14}})

    class P:
        def history(self, a, b, sy):
            return newer
    out = refresh({"base": "EUR", "currencies": ["USD"]}, str(path), provider=P(), today=date(2026, 1, 9))
    assert out.last_date == date(2026, 1, 8) and out.rates[date(2026, 1, 7)]["USD"] == 1.13 and len(out.rates) == 4
    assert SnapshotProvider(path).load().last_date == date(2026, 1, 8)
    assert merge(base, newer).last_date == date(2026, 1, 8)


def test_check_on_bundled_snapshot_runs_offline():
    s = SnapshotProvider(SNAP).load()
    check(load_config(ROOT / "config.yaml"), s, Store(), Alerter(url=""))


# ---------- app ----------
def test_streamlit_app_renders_offline_without_exceptions():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("../streamlit_app.py", default_timeout=90).run()
    assert not at.exception
    assert len(at.metric) == 4 and at.metric[0].label == "EUR/USD"
    at.number_input[0].set_value(10000).run()
    assert not at.exception
