from lighter_bot.trade import guard as G
from lighter_bot.trade.sizing import Stops, bucket, min_capital, sizes

US = 1_000_000


def lim(**kw):
    base = {"pos_stop_usd": 2.0, "daily_stop_usd": 5.0, "kill_usd": 25.0, "exit_taker_after_s": 20, "cooldown_s": 60}
    return G.Limits(**{**base, **kw})


def test_position_stop_exits_then_takes_then_cools_down():
    g = G.Guard(lim())
    t0 = 1_000 * 86_400 * US
    assert g.step(t0, 100, 0, None, 100).action == G.QUOTE
    d = g.step(t0 + US, 97.5, 1.0, 100.0, 97.5)          # 1 unit down 2.5 > $2
    assert d.action == G.EXIT and g.pos_stops == 1
    assert g.step(t0 + 10 * US, 97.5, 1.0, 100.0, 97.5).action == G.EXIT
    assert g.step(t0 + 22 * US, 97.5, 1.0, 100.0, 97.5).action == G.TAKER
    assert g.step(t0 + 23 * US, 97.5, 0, None, 97.5).action == G.IDLE      # flat: cooling down
    assert g.step(t0 + 90 * US, 97.5, 0, None, 97.5).action == G.QUOTE


def test_daily_stop_holds_until_the_next_utc_day():
    g = G.Guard(lim())
    t0 = 1_000 * 86_400 * US
    g.step(t0, 100, 0, None, 1)
    assert g.step(t0 + US, 94.9, 0, None, 1).action == G.IDLE
    assert g.state == "day_stopped"
    assert g.step(t0 + 86_400 * US, 94.9, 0, None, 1).action == G.QUOTE


def test_kill_from_the_peak():
    g = G.Guard(lim(kill_usd=10))
    t0 = 5 * 86_400 * US
    g.step(t0, 100, 0, None, 1)
    g.step(t0 + US, 112, 0, None, 1)
    d = g.step(t0 + 2 * US, 101, 1.0, 1.0, 1)
    assert d.action == G.TAKER and g.state == "killed"


def test_run_limits():
    g = G.Guard(lim(run_volume_usd=1000))
    t0 = 7 * 86_400 * US
    assert g.step(t0, 100, 1.0, 100.0, 100, run_pnl=0, run_vol=1500).action == G.EXIT
    assert g.step(t0 + US, 100, 0.0, None, 100, run_pnl=0, run_vol=1500).action == G.IDLE
    assert g.state == "done"
    g = G.Guard(lim(run_loss_usd=3))
    assert g.step(t0, 100, 1.0, 100.0, 100, run_pnl=-3.5, run_vol=0).action == G.TAKER
    assert g.state == "done"


def test_sizes_and_bounds():
    s = sizes(100, 50, Stops(2, 5, 25))
    assert (s.order_usd, s.cap_usd) == (2000, 4000)
    assert (s.pos_stop_usd, s.daily_stop_usd, s.kill_usd) == (2, 5, 25)
    s = sizes(10_000, 50, order_max=5000)                 # the liquidity ceiling binds
    assert s.order_usd == 5000 and s.capital == 250
    assert bucket(107) == 100 and bucket(0.95) == 0.9
    assert min_capital(10, 50) == 1.2 * 10 * 2.5 / 50
