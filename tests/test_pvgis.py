import pytest

from cable_pooling import pvgis


def test_parse_pv_yearly():
    hourly = [{"time": f"{y}0601:{h:02d}10", "P": 500.0, "G(i)": 600.0} for y in (2020, 2021) for h in range(4)]
    out = pvgis.parse_pv_yearly({"outputs": {"hourly": hourly}})
    assert list(out["year"]) == [2020, 2021]
    assert out["pvgis_kwh_kwp"].tolist() == pytest.approx([2.0, 2.0])         # 4 h x 0.5 kW per kWp
    assert out["pvgis_poa_kwh_m2"].tolist() == pytest.approx([2.4, 2.4])
