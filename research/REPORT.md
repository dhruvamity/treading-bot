# Research report: farming volume from a small account with limit orders

Status: **draft, updated as results arrive.** Live paper trading has not started: this cloud session's network
policy blocks every exchange (see [7](#7-live-paper-run-on-arcus)).

## 1. Summary

_Written last._

## 2. What was asked, and how it was tested

The goal: out of the Tread.fi strategies in the three data dumps, find the ones that turn a **small** account over
the **most** times, **within hours**, at or near **breakeven**, using **limit (maker) orders only**. Label the risk
of each, test them, and check the hypothesis that quoting both sides at the mid (Mid 0) is the best way.

Four kinds of evidence, strongest first:

| Tier | Evidence | Where |
|---|---|---|
| A | Real runs posted by Tread.fi users and the Tread team (46 sources) | [3](#3-tier-a-what-the-tread-fi-posts-show) and [01_strategy_shortlist.md](01_strategy_shortlist.md) |
| B | This repository's backtests on 4 real recorded days of Arcus books (Sep 20–23, 2026) | [4](#4-tier-b-the-repositorys-own-backtests-on-recorded-arcus-data) |
| C | The paper farm on synthetic model markets: mechanics only | [5](#5-tier-c-synthetic-mechanics-study) |
| D | The paper farm on live Arcus data (12–15 h) | [7](#7-live-paper-run-on-arcus) |

**The paper farm** (`bot farm`, `bot/bot/farm/`) runs every setting on the same data at once: 29 Tread-style
settings (Mid 0, join / improve the touch, Mid +1…+5, Grid +1…+10 with soft resets, a trailing RGrid, a Dynamic Grid
approximation, RSI-skewed Signal, and skip-US-session variants) at 5x, 10x, 20x and the market's maximum. Each
paper account has $100 and stops of 5% (position), 10% (day) and 20% (kill) of it, like the posts' 5–25% stops.
Fills use the scout's simulator: orders go live 150 ms after they are sent, and a resting order fills only when a
taker trades through its price, which is a lower bound on fills. The stops, order budget and liquidation rules are
the live bot's.

**The one formula that matters for this goal:**

> loss per day, in % of the capital = turnover per day × cost per $1M ÷ 10,000

Turnover is volume ÷ capital. A setup that trades 200× its capital per hour (Mid 0 at 10x does) trades 4,800× a day.
At a cost of $20 per $1M that is a 9.6% loss per day. **The more capital-efficient a setup is, the closer to zero
its cost per $1M must be.** A $237/1M setup (the RiseX Mid −1 runs) is fine at 50× a day (1.2%) and ruinous at
1,000× (24%).

## 3. Tier A: what the Tread.fi posts show

Details and all derivations: [01_strategy_shortlist.md](01_strategy_shortlist.md).

| Finding | Evidence |
|---|---|
| The best posted capital efficiency is a plain Grid on a venue with no builder fee: **525× the capital per day at ~$24 per $1M** (~1.2% of capital per day) | S20: $1.73M on $1,650 in 2 days, comp PnL −$40.80 (RiseX) |
| DGrid in calm hours had a **trading profit** (−0.35 bp); its whole cost was the 1 bp fee | S02: 9 BTC DGrid rows, $185k, fees $18.88, PnL +$6.49 |
| Mid −1 aggressive is the **reliable cost** setting: 19 of 19 runs lost, but in a tight band ($121–369 per $1M; 1.0 bp fee + 1.37 bp trading loss) | S04: $3.18M on RiseX BTC |
| Tight Mid on a volatile alt is the **fast way to lose the account**: −42% in a week at 152× capital per day | S03: Perpl SOL Mid −1..−3, $788k, win rate 20.9% |
| Every post at Mid ≤ 0 shows a **trading loss before fees** | S03 (3.25 bp), S04 (1.37 bp), S07 (Grid 0–1 bps "result in losses") |
| Wider settings win per trade but trade less; outright failures come from volatile periods and high leverage | S05 (every Mid +5..+50 / Grid +10..+20 run stopped out, $810–25,550 per $1M), S15 ("high leverage or over $100 it wrecks me") |
| On Tread, fees are most of the posted costs (1–3.6 bp); **Arcus charges makers nothing** | S04, S29, S11; Arcus fee tiers |

## 4. Tier B: the repository's own backtests on recorded Arcus data

These are the owner's measurements on 4 full recorded days (2026-09-20 to 09-23, 19 markets), with the same
simulator the farm uses, documented in the top-level README (sections 4.6 and 7.8). They are the only real Arcus
data available to this session.

| Result on real Arcus books | Number |
|---|---|
| The most maker volume per dollar near breakeven at $100: `deep 3bp, skew` on QQQ at 20x | $20,961 a day (**210× capital per day**, ~8.7× per hour), +$0.29/day, worst day −$1.60 |
| Aggressive Mid (`improve touch`, `touch 1bp`, the equivalents of Tread's Mid −1 / aggressive) | **$30–130 per $1M**: about Tread's trading loss, without Tread's fees |
| Skipping 09:00–16:30 New York time (Tread: "avoid NYC hours") | Better in 339 of 455 market × setting × leverage combinations; cost 1.19 → 0.72 bp; kept ~70% of the volume |
| Grid around the last fill (Tread's Grid) | Loses 2.4–4.0 bp per dollar without a soft reset; about breakeven with a 0.1% soft reset; best on NVDA and SLV |
| BTC / crypto majors with a DGrid-style setup | Lost in every hour of the day (−2.3 bp) |
| Pausing quotes on volatility | Hurt: the pause pulled quotes exactly when sweeps revert, which is where deep quotes earn |
| Capital scaling | $50–1,000: volume grows about in step with capital (100–230× capital a day); beyond ~$1,000 the markets' liquidity caps it |

## 5. Tier C: synthetic mechanics study

_Filled in from `research/synthetic/`._

## 6. The Mid 0 hypothesis

_Filled in after tiers C and D._

## 7. Live paper run on Arcus

_Pending: blocked by the session's network policy (api.arcus.xyz denied)._

## 8. Recommendation

_Written last._

## 9. Caveats

- Tier A is self-reported, often from referral posts; the shortlist labels confidence per source.
- Tier B covers 4 days. Tier C is a model. Only tier D (live) measures today's Arcus market.
- The fill model is conservative: a trade at our exact price never fills us. That undercounts fills most for
  settings that join the touch (Mid 0 on a one-tick book). The `--front-of-queue` re-analysis gives the upper bound.
- No backtest sees how our own quotes change other traders' behaviour. On books as thin as Arcus's (QQQ trades
  about $1M a day), a $400 order at the touch is visible and may be traded against differently.
