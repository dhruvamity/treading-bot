# deltaneutral

Two markets held delta-neutral across Arcus and Lighter (Robinhood Chain), renewed every 24 hours. On Arcus one
market is long and the other short; on Lighter the opposite, with the same units of each market on both venues. So
the whole is flat in both markets, and each venue's money moves only with the difference between the two.

Example, USO/ORCL with $100 on each venue: Arcus long $450 USO and short $450 ORCL, Lighter short $450 USO and long
$450 ORCL. That is $1,800 of open interest across the four legs and about $3,600 traded a day.

## What the research says

`reports/REPORT.md` (`deltaneutral research`) has the details. In brief, on the last 30 days of both venues' own
prices and funding, with $100 on each:

| Pair | $200 became | Traded | Open interest held | Stops / guard closes | Transfers |
|---|---|---|---|---|---|
| USO/ORCL (the default) | $188.60 | $102k | $1,666 | 0 / 1 | 30, typically $13 |
| ORCL/CRWV | $184.08 | $95k | $1,442 | 6 / 1 | 30, typically $8 |
| AMD/PLTR (lost least) | $194.63 | $97k | $1,588 | 1 / 0 | 30, typically $5 |

- **No pair makes money** when it closes and reopens every 24 hours. The eight fills a day cost more than the
  funding pays. Limit orders on both venues save $5-12 a month against taking every fill.
- **The one real edge is USO.** Arcus pays USO's longs about 14% a year, while Lighter charges them about 4%. So the
  bot is long USO on Arcus and short it on Lighter.
- **USO/ORCL fits the brief.** USO is Arcus's highest leverage of the eight (20x), and ORCL is its lowest open
  interest at 10x. Its limit orders fill least often on Lighter, though; AMD's fill most often.
- **The risk is earnings.** At the highest leverage, about 1 day in 5 ended early over the longer stretch since
  mid-July, mostly around earnings. Without the stops, about 1 day in 14 would have liquidated a venue.
- **Skip earnings days.** `deltaneutral skip DATE` keeps the bot flat around a report.

## How a cycle works

1. **Open.** At `open_ny` (10:00 New York), the bot opens the largest position both venues' margins allow.
   - Arcus asks 1.5 times the margin while the stock market is closed. So an off-hours open is smaller, and Lighter's
     legs are as small as Arcus's.
   - The bot decides the way round itself at every open, from the funding it fetches then: each venue's hourly
     rates over the last 7 days (`funding_days`), applied hour by hour to the coming 24 hours, the stock market's
     open hours apart from its closed ones (Arcus charges one fixed rate while it is closed). The opening message
     says what that should earn, for example `+$0.30 over the 24 hours`.
   - It turns round only at an open: turning mid-cycle costs eight fills (about $0.70), more than a day's funding.
2. **Limit orders on both venues** (`execution chase`). Every leg rests a limit order that chases its own book.
   - Price: the best price, or one tick inside it when the spread is wider than two ticks. It moves when the best
     price moves (`requote_s`, 10 s), and Lighter's are replaced before their 5.5-minute expiry.
   - Size: it follows the other venue. Neither venue gets more than `max_gap_usd` ($150) of a market ahead of the
     other, and a gap older than `catchup_s` (60 s) is closed by the venue behind taking it.
   - An entry still chasing after `enter_timeout_s` (30 min) keeps what both venues hold.
   - `execution arcus` rests only Arcus's legs (Lighter takes to stay level); `execution take` takes everything.
3. **Stops at 75% of the way to liquidation** (`stop_share`). Once open, each leg gets a stop and a take-profit
   resting on its venue.
   - They sit at the same two prices on both venues, so whichever venue's fires, the other's fires at that price too
     and the market stays hedged.
   - The distance is 75% of the way to where that market alone would liquidate the weaker venue. For Lighter that is
     its maintenance margin (6%), where its partial liquidation starts.
   - The bot also closes everything once a venue has used 75% of its room, or once a venue no longer holds a leg (its
     stop fired, or it was liquidated). These closes take at once.
   - Lighter and Arcus are kept level in every market, whichever one changed.
4. **Close.** After `hold_h` hours (24), the stops are cancelled and both venues chase again, reduce-only. Whatever is
   left after `exit_timeout_s` (15 min) is taken.
5. **Transfer.** After the close it reads both venues' money and sends one line to Telegram, for example:
   `MOVE $13.17 from Arcus (0x12ab…cd34, subaccount 0) to Lighter (account 12345)`.
   - Both venues hold USDG on Robinhood Chain. Withdraw from one to your wallet, then deposit on the other.
   - The next cycle opens at once, sized by the poorer venue. With `wait_transfer 1`, it waits for the move instead.

About Arcus's lower leverage after hours: Arcus does not cut a position at the close. Its docs say maintenance
margin is unchanged off-hours, and only opening or adding needs the higher margin. So by default the bot keeps the
position, and only a cycle opened while the market is closed is smaller. `deltaneutral set offhours cut` cuts both
venues to Arcus's off-hours size at each close and grows them back at the reopen (the stops are placed again). In
the backtest that only added fills.

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
deltaneutral set stop_share 0.75        # any setting (deltaneutral settings lists them)
deltaneutral close | pause | resume     # the running bot
deltaneutral stop                       # stops it (an open cycle stays open, with its stops on the venues)
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
  Arcus's has never sent a real order, and its stop orders (`positionTpsl`) have never been placed live. If a venue
  refuses a stop, the bot says so.
- **Stops if the bot stops.** Each leg's stop and take-profit stay on its venue at the same prices on both venues, so
  a market closes on both. The other market then stays open on both venues, still hedged, until the bot runs again.

## Files

| Where | What |
|---|---|
| `data/` | `deltaneutral fetch`: five-minute prices, hourly funding, Arcus's trades by the minute, hourly history since late June, `markets.json`, `costs.json` |
| `reports/REPORT.md` | the research and the 30-day backtest |
| `state/<mode>-*.json(l)` | the bot's state, events, cycles and commands; `run-<mode>.pid` and `.out` while it runs in the background |
| `settings.json` | your settings |

Code: `plan.py` (sizing, room to liquidation, the transfer), `engine.py` (the cycle, the limit orders, the stops),
`venues.py` (paper and live venues), `bot.py` (the loop), `backtest.py`, `research.py`, `costs.py`, `data.py`,
`series.py`.
