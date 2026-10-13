"""'What is my invoice worth?': conversion + scenario table."""
from __future__ import annotations

import pandas as pd

from .analytics import pair

SHOCKS = [-0.05, -0.03, -0.01, 0.0, 0.01, 0.03, 0.05]


def invoice_value(wide: pd.DataFrame, amount: float, invoice_ccy: str, home_ccy: str) -> tuple[float, float]:
    """-> (value in home currency, rate used). Rate = home units per 1 invoice unit."""
    rate = float(pair(wide, invoice_ccy, home_ccy).iloc[-1])
    return amount * rate, rate


def scenarios(wide: pd.DataFrame, amount: float, invoice_ccy: str, home_ccy: str, horizon: int = 30) -> pd.DataFrame:
    """Illustrative table: (a) +/- shocks to today's rate, (b) the 5th/50th/95th percentile of historical
    `horizon`-day moves of this pair applied to today's rate. Descriptive, not a forecast."""
    s = pair(wide, invoice_ccy, home_ccy)
    now = float(s.iloc[-1])
    rows = [{"scenario": f"rate {x:+.0%}", "rate": now * (1 + x), "value": amount * now * (1 + x),
             "vs today": amount * now * x} for x in SHOCKS]
    moves = (s / s.shift(horizon) - 1).dropna()
    if len(moves) >= 30:
        for label, q in (("historical worst 5%", .05), ("historical median", .5), ("historical best 5%", .95)):
            x = float(moves.quantile(q))
            rows.append({"scenario": f"{label} ({horizon}d move {x:+.1%})", "rate": now * (1 + x),
                         "value": amount * now * (1 + x), "vs today": amount * now * x})
    return pd.DataFrame(rows)
