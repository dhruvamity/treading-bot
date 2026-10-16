"""plan.py: the size both venues allow, room to liquidation, which way pays, and the transfer."""

import datetime as dt
import math

from deltaneutral import plan as P
from deltaneutral.plan import ARCUS, LIGHTER
from deltaneutral.series import arcus_open

TEN = {ARCUS: P.Rules(imf=0.1, imf_off=0.15, mmf=0.0667, taker_bp=2.25),
       LIGHTER: P.Rules(imf=0.1, imf_off=0.1, mmf=0.06)}
TWENTY = {ARCUS: P.Rules(imf=0.05, imf_off=0.075, mmf=0.0333), LIGHTER: P.Rules(imf=0.1, imf_off=0.1, mmf=0.06)}


def test_the_size_is_what_the_venue_with_less_room_allows():
    s = P.size({ARCUS: 100.0, LIGHTER: 100.0}, TEN, TEN, off_hours=False, margin_use=0.9)
    assert math.isclose(s.nx, 450) and math.isclose(s.ny, 450)          # 90 of margin / (10% + 10%)
    assert math.isclose(s.leverage[ARCUS], 9.0) and math.isclose(s.leverage[LIGHTER], 9.0)


def test_off_hours_arcus_asks_more_margin_and_lighter_follows_its_size():
    s = P.size({ARCUS: 100.0, LIGHTER: 100.0}, TEN, TEN, off_hours=True, margin_use=0.9)
    assert s.binding == ARCUS and math.isclose(s.nx, 300)               # 90 / (15% + 15%)
    assert math.isclose(s.leverage[LIGHTER], 6.0)


def test_twenty_x_on_arcus_does_not_help_when_lighter_allows_ten():
    s = P.size({ARCUS: 100.0, LIGHTER: 100.0}, TWENTY, TEN, off_hours=False, margin_use=0.9)
    assert s.binding == LIGHTER and math.isclose(s.nx, 450)


def test_the_poorer_venue_sets_the_size_and_a_cap_holds():
    s = P.size({ARCUS: 150.0, LIGHTER: 50.0}, TEN, TEN, off_hours=False, margin_use=0.9)
    assert s.binding == LIGHTER and math.isclose(s.nx, 225)
    capped = P.size({ARCUS: 100.0, LIGHTER: 100.0}, TEN, TEN, off_hours=False, margin_use=0.9, max_leverage=3)
    assert math.isclose(capped.nx, 150)                                 # 3 x 100 / 2 markets


def test_room_and_how_far_the_pair_may_move_apart():
    r = TEN[LIGHTER]
    assert math.isclose(P.maintenance(450, 450, r, r), 54.0)
    assert math.isclose(P.room(100, 450, 450, r, r), (100 - 54) / 54)
    assert math.isclose(P.spread_to_liquidation(100, 450, 450, r, r), 46 / 450)


def test_way_shorts_x_on_the_venue_whose_funding_is_higher():
    assert P.way_from(diff_x=0.00002, diff_y=0.0) == 1       # Lighter pays more on X: X short on Lighter
    assert P.way_from(diff_x=0.0, diff_y=0.00002) == -1
    sd = P.sides(1)
    assert sd[ARCUS] == {"x": 1, "y": -1} and sd[LIGHTER] == {"x": -1, "y": 1}


def test_the_funding_forecast_judges_each_hour_of_the_hold_by_its_session():
    """Arcus pays X's longs while the stock market is open and charges its one fixed rate while it is closed, when
    Lighter pays X's shorts: a weekday hold should be long X on Arcus, a weekend hold (closed hours only) the other
    way round. The last 24 hours before Saturday's open (mostly Friday) would have said the weekday way."""
    yr = 24 * 365 * 100
    sat = dt.datetime(2026, 10, 17, 14, tzinfo=dt.UTC).timestamp()        # Saturday 10:00 New York
    hist: dict = {(v, m): {} for v in (ARCUS, LIGHTER) for m in ("X", "Y")}
    for h in range(int(sat) - 7 * 86_400, int(sat), 3600):
        o = arcus_open(h)
        hist[(ARCUS, "X")][h] = (-20 if o else 5) / yr
        hist[(LIGHTER, "X")][h] = (4 if o else -2) / yr
        hist[(ARCUS, "Y")][h] = 5 / yr
        hist[(LIGHTER, "Y")][h] = 4 / yr
    weekday = P.forecast(hist, "X", "Y", sat - 3 * 86_400, 24, arcus_open)      # Wednesday 10:00
    weekend = P.forecast(hist, "X", "Y", sat, 24, arcus_open)
    assert weekday is not None and weekend is not None
    assert weekday.way == 1 and weekend.way == -1
    assert abs(weekday.edge * yr - (16 * 25 - 8 * 6) / 24) < 1e-6            # 16 open hours, 8 closed
    assert abs(weekend.edge * yr - 6) < 1e-6 and abs(weekend.rates[(ARCUS, "X")] * yr - 5) < 1e-6
    hist.pop((LIGHTER, "Y"))
    assert P.forecast(hist, "X", "Y", sat, 24, arcus_open) is None             # a market without history


def test_the_transfer_evens_the_money_to_the_cent():
    t = P.transfer({ARCUS: 112.37, LIGHTER: 81.40})
    assert (t.amount, t.source, t.target) == (15.48, ARCUS, LIGHTER)
    assert "MOVE $15.48 from Arcus to Lighter" in t.text()
    even = P.transfer({ARCUS: 100.0, LIGHTER: 100.004})
    assert even.amount == 0 and "nothing to move" in even.text()
    small = P.transfer({ARCUS: 100.0, LIGHTER: 101.0}, min_usd=1.0)
    assert small.amount == 0
