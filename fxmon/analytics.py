"""Pair series, unusual-move z-scores, rolling volatility, N-day extremes."""
from __future__ import annotations

import numpy as np
import pandas as pd


def pair(wide: pd.DataFrame, base: str, quote: str) -> pd.Series:
    """Units of `quote` per 1 `base`, via the EUR legs (ECB publishes everything against EUR)."""
    return (wide[quote] / wide[base]).dropna().rename(f"{base}/{quote}")


def daily_pct(s: pd.Series) -> pd.Series:
    return s.pct_change() * 100


def zscore_moves(s: pd.Series, window: int = 60) -> pd.Series:
    """z-score of each day's % change vs the mean/std of the *previous* `window` changes (no look-ahead)."""
    chg = daily_pct(s)
    prev = chg.shift(1).rolling(window, min_periods=max(10, window // 3))
    return ((chg - prev.mean()) / prev.std()).rename("z")


def rolling_vol(s: pd.Series, window: int = 20, periods: int = 252) -> pd.Series:
    """Annualised volatility (%) of daily log returns over a rolling window."""
    r = np.log(s).diff()
    return (r.rolling(window).std() * np.sqrt(periods) * 100).rename("vol")


def n_day_extreme(s: pd.Series, n: int = 30) -> str | None:
    """'high' / 'low' if the latest value is beyond every one of the previous n observations."""
    if len(s) <= n:
        return None
    last, prev = s.iloc[-1], s.iloc[-n - 1:-1]
    return "high" if last > prev.max() else "low" if last < prev.min() else None


def movers(wide: pd.DataFrame, base: str, vol_window: int = 20) -> pd.DataFrame:
    rows = []
    for q in wide.columns:
        if q == base:
            continue
        s = pair(wide, base, q)
        if len(s) < 35:
            continue
        rows.append({"pair": f"{base}/{q}", "rate": s.iloc[-1], "1d %": daily_pct(s).iloc[-1],
                     "30d %": (s.iloc[-1] / s.iloc[-31] - 1) * 100, "vol 20d %": rolling_vol(s, vol_window).iloc[-1],
                     "z (1d move)": zscore_moves(s).iloc[-1], "30d range pos": (s.iloc[-1] - s.iloc[-30:].min()) /
                     max(s.iloc[-30:].max() - s.iloc[-30:].min(), 1e-12)})
    return pd.DataFrame(rows).set_index("pair")
