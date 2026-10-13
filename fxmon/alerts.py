"""Rule evaluation (thresholds, N-day high/low, unusual move) + mocked Slack/Discord-compatible webhook."""
from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd
import requests
import yaml

from .analytics import n_day_extreme, pair, zscore_moves
from .store import Store


@dataclass
class Alert:
    key: str
    kind: str
    pair: str
    message: str


def load_config(path="config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def evaluate(wide: pd.DataFrame, cfg: dict) -> list[Alert]:
    out: list[Alert] = []
    base = cfg.get("base", "EUR")
    for p in cfg.get("pairs", []):
        b, q = (p.split("/") + [base])[:2] if "/" in p else (base, p)
        if b not in wide or q not in wide:
            continue
        s = pair(wide, b, q)
        if len(s) < 2:
            continue
        day, last = s.index[-1].date(), float(s.iloc[-1])
        name = f"{b}/{q}"
        for t in cfg.get("thresholds", []):
            if t["pair"] != name:
                continue
            if "above" in t and last > t["above"] >= float(s.iloc[-2]):
                out.append(Alert(f"{name}:above:{t['above']}:{day}", "threshold", name, f"{name} crossed above {t['above']} ({last:.4f}) on {day}"))
            if "below" in t and last < t["below"] <= float(s.iloc[-2]):
                out.append(Alert(f"{name}:below:{t['below']}:{day}", "threshold", name, f"{name} crossed below {t['below']} ({last:.4f}) on {day}"))
        n = cfg.get("nday", 30)
        ext = n_day_extreme(s, n)
        if ext:
            out.append(Alert(f"{name}:{n}d-{ext}:{day}", f"{n}d_{ext}", name, f"{name} at a {n}-day {ext} ({last:.4f}) on {day}"))
        z = zscore_moves(s).iloc[-1]
        if pd.notna(z) and abs(z) >= cfg.get("zscore", 2.5):
            chg = (s.iloc[-1] / s.iloc[-2] - 1) * 100
            out.append(Alert(f"{name}:z:{day}", "unusual_move", name, f"{name} unusual move {chg:+.2f}% on {day} (z={z:+.1f} vs trailing 60d)"))
    return out


class Alerter:
    """Mock by default (prints). Real Slack/Discord webhook only if ALERT_WEBHOOK_URL is set in the environment."""

    def __init__(self, url: str | None = None, kind: str | None = None):
        self.url = url if url is not None else os.getenv("ALERT_WEBHOOK_URL")
        self.kind = kind or os.getenv("ALERT_WEBHOOK_KIND", "slack")
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        if not self.url:
            print(f"[mock-alert] {text}")
            return
        body = {"content": text} if self.kind == "discord" else {"text": text}
        requests.post(self.url, json=body, timeout=10).raise_for_status()


def dispatch(alerts: list[Alert], store: Store, alerter: Alerter, ts: str) -> list[Alert]:
    """Send each alert once (dedupe by key across scheduled runs)."""
    fresh = [a for a in alerts if not store.already_sent(a.key)]
    for a in fresh:
        alerter.send(f"FX alert (reference rates, not advice): {a.message}")
        store.mark_sent(a.key, ts, a.message)
    return fresh
