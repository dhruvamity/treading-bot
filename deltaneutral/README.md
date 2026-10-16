# deltaneutral

Two markets held delta-neutral across Arcus and Lighter (Robinhood Chain), renewed every 24 hours. On Arcus one
market is long and the other short; on Lighter the opposite, with the same units of each market on both venues. So
the whole is flat in both markets, and each venue's money moves only with the difference between the two.

Example, USO/ORCL with $100 on each venue: Arcus long $450 USO and short $450 ORCL, Lighter short $450 USO and long
$450 ORCL. That is $1,800 of open interest across the four legs and about $3,600 traded a day.

## What the research says

`reports/REPORT.md` (`deltaneutral research`) has the details. In brief, on the last 30 days of both venues' own
prices and funding, with $100 on each:

| Pair | $200 became | Traded | Open interest held | Early closes | Transfers |
|---|---|---|---|---|---|
| USO/ORCL (the default) | $187.46 | $102k | $1,661 | 1 | 30, typically $13 |
| ORCL/CRWV | $183.29 | $94k | $1,502 | 3 | 30, typically $6 |
| COIN/USO (lost least) | $193.65 | $78k | $1,193 | 4 | 30, typically $7 |

- **No pair makes money** when it closes and reopens every 24 hours. The eight fills a day cost more than the
  funding pays.
- **The one real edge is USO.** Arcus pays USO's longs about 14% a year, while Lighter charges them about 4%. So the
  bot is long USO on Arcus and short it on Lighter.
- **USO/ORCL fits the brief.** USO is Arcus's highest leverage of the eight (20x), and ORCL is its lowest open
  interest at 10x.
- **The risk is earnings.** At the highest leverage, the guard closed about 1 day in 5 early over the longer stretch
  since mid-July, mostly around earnings. Without the guard, about 1 day in 14 would have liquidated a venue.
- **Skip earnings days.** `deltaneutral skip DATE` keeps the bot flat around a report.

## How a cycle works

1. **Open.** At `open_ny` (10:00 New York), the bot opens the largest position both venues' margins allow.
   - Arcus asks 1.5 times the margin while the stock market is closed. So an off-hours open is smaller, and Lighter's
     legs are as small as Arcus's.
   - The way round is the one the funding pays.
   - Arcus's legs rest limit orders at the best price first, which carries no fee. After `maker_wait_s` the rest is
     taken.
   - Lighter's legs follow what Arcus holds, by taker orders. Lighter charges no fee.
2. **Open, every loop.** Lighter is made the mirror of Arcus again. So anything that changes Arcus's position changes
   Lighter's the same way. That covers a partial fill, a liquidation, your own order, and `offhours cut` at the stock
   market's close.
   - If a venue's room to liquidation falls under `guard` (0.25), the bot closes everything at once.
3. **Close.** After `hold_h` hours (24), the bot closes the same way, reduce-only.
4. **Transfer.** After the close it reads both venues' money and sends one line to Telegram, for example:
   `MOVE $13.17 from Arcus (0x12ab…cd34, subaccount 0) to Lighter (account 12345)`.
   - Both venues hold USDG on Robinhood Chain. Withdraw from one to your wallet, then deposit on the other.
   - The next cycle opens at once, sized by the poorer venue. With `wait_transfer 1`, it waits for the move instead.

About Arcus's lower leverage after hours: Arcus does not cut a position at the close. Its docs say maintenance
margin is unchanged off-hours, and only opening or adding needs the higher margin. So by default the bot keeps the
position, and only a cycle opened while the market is closed is smaller. `deltaneutral set offhours cut` cuts both
venues to Arcus's off-hours size at each close and grows them back at the reopen. In the backtest that only added
fills.

## Commands

All of these run from `treading-bot/deltaneutral`; the program is `../arcus/.venv/bin/deltaneutral` until
`make install` has run.

```bash
deltaneutral pair USO ORCL              # the two markets
deltaneutral start --now                # paper bot in the background; the first cycle opens at once
deltaneutral status                     # what it holds, the last cycle, the transfer asked for
deltaneutral transfer                   # how much to move, from which venue to which
deltaneutral cycles                     # the last cycles
deltaneutral skip 2026-10-22            # an earnings date: flat from 16:00 New York the day before to the day after
deltaneutral set guard 0.3              # any setting (deltaneutral settings lists them)
deltaneutral close | pause | resume     # the running bot
deltaneutral stop                       # stops it (an open cycle stays open)
deltaneutral start --live               # REAL orders: needs DN_LIVE=1 in arcus/.env and LIVE typed here
deltaneutral backtest USO ORCL --cycles # one pair's 30 days, cycle by cycle (--help for the options)
deltaneutral fetch && deltaneutral costs && deltaneutral research   # fresh data, costs and report
```

From the rest of the bot: `tbot up deltaneutral` starts the paper bot and `tbot down deltaneutral` stops it. A plain
`tbot up` does not start it. In the one Telegram bot, `/dn` lists its commands:

- `/dn_status`, `/dn_cycles`, `/dn_transfer`
- `/dn_settings`, `/dn_set NAME VALUE`, `/dn_pair USO ORCL`, `/dn_skip DATE`
- `/dn_close`, `/dn_pause`, `/dn_resume`
- `/dn_start`, `/dn_stop` (paper only)

Add `live` to act on the live bot, for example `/dn_status live`.

## Before real money

- **Accounts.** Give it accounts the market-making bots and the arbitrage are not using: an Arcus subaccount of its
  own, and its own Lighter API key. Two programs on one account would each treat the other's positions as theirs.
- **First run.** Run with `max_notional_usd` set small, for example `deltaneutral set max_notional_usd 20`, for one
  cycle.
- **Order code.** Live uses the arbitrage's order clients. Lighter's passed `arbitrage livetest` on the venue;
  Arcus's has never sent a real order.
- **No venue-side stop.** There is no stop order resting on the venues for this bot. If the bot stops, the positions
  stay hedged across the venues, but no guard watches them.

## Files

| Where | What |
|---|---|
| `data/` | `deltaneutral fetch`: five-minute prices, hourly funding, Arcus's trades by the minute, hourly history since late June, `markets.json`, `costs.json` |
| `reports/REPORT.md` | the research and the 30-day backtest |
| `state/<mode>-*.json(l)` | the bot's state, events, cycles and commands; `run-<mode>.pid` and `.out` while it runs in the background |
| `settings.json` | your settings |

Code: `plan.py` (sizing, room to liquidation, the transfer), `engine.py` (the cycle), `venues.py` (paper and live
venues), `bot.py` (the loop), `backtest.py`, `research.py`, `costs.py`, `data.py`, `series.py`.
