from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pse_opendata as pse
import pytest

from cable_pooling import pse_api


def records(day="2025-06-01", n=8, start_price=100.0):
    out = []
    t0 = pd.Timestamp(f"{day} 00:00")
    for i in range(n):
        end = t0 + pd.Timedelta(minutes=15 * (i + 1))
        out.append({"dtime_utc": end.strftime("%Y-%m-%d %H:%M:%S"), "rce_pln": start_price + i,
                    "business_date": day})
    return out


class Resp:
    def __init__(self, data):
        self.status_code, self.headers, self.reason, self.data = 200, {}, "OK", data

    def json(self):
        return self.data


class Session:
    """Answers the business_date filters of pse-opendata from fixed records; records every request."""

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        lo, hi = [part.split("'")[1] for part in params["$filter"].split(" and ")]
        return Resp({"value": [r for r in self.rows if lo <= r["business_date"] <= hi]})


def fetch(rows, tmp_path, start=date(2025, 6, 1), end=date(2025, 6, 1)):
    session = Session(rows)
    client = pse.Client(session=session, cache_dir=tmp_path, now=lambda: datetime(2025, 7, 1, tzinfo=timezone.utc),
                        sleep=lambda s: None, min_interval=0, retries=1)
    return pse_api.fetch_range(start, end, tmp_path, client=client), session


def test_fetch_shifts_end_to_start(tmp_path):
    df, _ = fetch(records(), tmp_path)
    assert df.index[0] == pd.Timestamp("2025-06-01 00:00", tz="UTC")
    assert df.index[-1] == pd.Timestamp("2025-06-01 01:45", tz="UTC")
    assert df["price"].iloc[0] == 100.0
    assert df["business_date"].iloc[0] == date(2025, 6, 1)


def test_fetch_empty(tmp_path):
    df, _ = fetch([], tmp_path)
    assert df.empty and "price" in df.columns


def test_whole_months_are_requested_and_cut(tmp_path):
    rows = records() + records(day="2025-06-02") + records(day="2025-06-30")
    df, session = fetch(rows, tmp_path, end=date(2025, 6, 2))
    assert session.calls[0][1]["$filter"] == "business_date ge '2025-06-01' and business_date le '2025-06-30'"
    assert len(df) == 16 and set(df["business_date"]) == {date(2025, 6, 1), date(2025, 6, 2)}
    assert (tmp_path / "api" / "rce-pln" / "2025-06.parquet").exists()


def test_hourly_mean_and_incomplete_hours(tmp_path):
    df, _ = fetch(records(n=7), tmp_path)      # second hour has only three quarters
    h = pse_api.to_hourly(df)
    assert len(h) == 1
    assert h.iloc[0] == pytest.approx(np.mean([100, 101, 102, 103]))
    assert h.index[0] == pd.Timestamp("2025-06-01 00:00", tz="UTC")


@pytest.mark.parametrize("day, hours", [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25), (date(2025, 6, 1), 24)])
def test_economic_hours_dst(day, hours):
    idx = pse_api.economic_hours(day, day)
    assert len(idx) == hours
    assert idx[0].tz_convert("Europe/Warsaw").hour == 0


def test_two_operating_years_are_17520_hours():
    assert len(pse_api.economic_hours(date(2024, 10, 1), date(2026, 9, 30))) == 17520
