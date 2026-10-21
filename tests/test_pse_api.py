from datetime import date

import numpy as np
import pandas as pd
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


def test_parse_shifts_end_to_start():
    df = pse_api.parse_records(records())
    assert df.index[0] == pd.Timestamp("2025-06-01 00:00", tz="UTC")
    assert df.index[-1] == pd.Timestamp("2025-06-01 01:45", tz="UTC")
    assert df["price"].iloc[0] == 100.0
    assert df["business_date"].iloc[0] == date(2025, 6, 1)


def test_parse_empty():
    df = pse_api.parse_records([])
    assert df.empty and "price" in df.columns


def test_hourly_mean_and_incomplete_hours():
    df = pse_api.parse_records(records(n=7))      # second hour has only three quarters
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


def test_pagination():
    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class Session:
        headers: dict = {}

        def __init__(self):
            self.urls = []

        def get(self, url, params=None, timeout=None):
            self.urls.append(url)
            if len(self.urls) == 1:
                return Resp({"value": records(n=4), "nextLink": "https://next"})
            return Resp({"value": records(n=8)[4:]})

    s = Session()
    s.headers = {}
    df = pse_api.fetch_period(date(2025, 6, 1), date(2025, 6, 1), session=s)
    assert s.urls == [f"{pse_api.BASE_URL}/{pse_api.ENDPOINT}", "https://next"]
    assert len(df) == 8
