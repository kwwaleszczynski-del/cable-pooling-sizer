from datetime import date

import pandas as pd
import pytest

from cable_pooling import openmeteo as om
from cable_pooling.config import Site

SITE = Site("t", "Test", 52.0, 19.0)


def payload(n=6):
    times = [f"2025-06-01T{h:02d}:00" for h in range(n)]
    return {
        "elevation": 120.0,
        "hourly": {
            "time": times,
            "shortwave_radiation": [0, 10, 100, 200, 300, 400][:n],
            "diffuse_radiation": [0, 5, 50, 80, 100, 120][:n],
            "temperature_2m": [10, 12, 14, 16, 18, 20][:n],
            "wind_speed_10m": [2, 3, 4, 5, 6, 7][:n],
            "wind_speed_100m": [4, 6, 8, 10, 12, 14][:n],
        },
    }


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data
        self.text = ""

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        assert params["models"] == "era5" and params["timezone"] == "GMT"
        return FakeResponse(self.data)


def test_request_weight():
    assert om.request_weight(5, date(2025, 1, 1), date(2025, 1, 14)) == 1.0
    assert om.request_weight(10, date(2025, 1, 1), date(2025, 1, 28)) == pytest.approx(2.0)
    assert om.request_weight(5, date(2020, 1, 1), date(2026, 9, 30)) == pytest.approx(0.5 * 2465 / 14)


def test_parse_response():
    df = om.parse_response(payload())
    assert list(df.columns[:5]) == ["ghi", "dhi", "temp_air", "wind_10m", "wind_100m"]
    assert df.index[0] == pd.Timestamp("2025-06-01 00:00", tz="UTC")
    assert (df["elevation_m"] == 120.0).all()


def test_intervals_follow_start_convention():
    out = om.to_intervals(om.parse_response(payload()))
    # radiation stamped 03:00 is the mean of 02:00-03:00 -> interval starting 02:00
    assert out.loc[pd.Timestamp("2025-06-01 02:00", tz="UTC"), "ghi"] == 200
    # instantaneous temperature: mean of the two ends of the interval
    assert out.loc[pd.Timestamp("2025-06-01 02:00", tz="UTC"), "temp_air"] == pytest.approx(15.0)
    # the last stamp has no following value and is dropped
    assert out.index.max() == pd.Timestamp("2025-06-01 04:00", tz="UTC")
    assert len(out) == 5


def test_fetch_uses_cache_and_ledger(tmp_path):
    session = FakeSession(payload())
    a = om.fetch_site(SITE, date(2025, 6, 1), date(2025, 6, 1), tmp_path, session=session)
    b = om.fetch_site(SITE, date(2025, 6, 1), date(2025, 6, 1), tmp_path, session=session)
    assert session.calls == 1
    pd.testing.assert_frame_equal(a, b, check_freq=False)
    assert (tmp_path / "openmeteo_ledger.json").exists()


def test_budget_refuses_large_request(tmp_path):
    session = FakeSession(payload())
    with pytest.raises(RuntimeError, match="budget"):
        om.fetch_site(SITE, date(2000, 1, 1), date(2025, 12, 31), tmp_path, max_daily_weight=50, session=session)
    assert session.calls == 0
