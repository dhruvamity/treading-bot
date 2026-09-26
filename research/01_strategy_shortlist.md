# 1. Strategy shortlist: volume from little capital, near breakeven

Source: the three Tread.fi data dumps supplied on 2026-09-25 (`raw.md`, `raw2.md`, `treadfi_bot_configs.md`;
46 source posts S01–S46). Every number below is either copied from a post or screenshot, or marked **derived**
with the formula. The derivations were computed with a script, not by hand.

## 1.1 What was kept

The goal is **a lot of volume per dollar of margin, within hours, at or near breakeven**. A configuration was kept
when it is a market-making (limit-order) setup that turns margin over quickly: Mid, Grid, RGrid, DGrid or Signal.
It did not have to be profitable. Loss-making setups are kept and labelled, not discarded.

Left out, with the reason:

| Left out | Why |
|---|---|
| Delta-neutral bots (S23, S24, S25, S43, S44) | Two venues, a long hold, and "more capital, more time, and higher cost" (S02, who switched away from DN). The point of DN is funding, not turnover. |
| Blend mode (S13) | Needs an outside reference price; no user posted a run. |
| Reward and airdrop economics (S16, S22, S40–S42) | Not a strategy. They are used only to explain why people accept a $100–400/1M cost. |

## 1.2 How risk is labelled

**Cost per $1M (CPM)** = (fees − trading PnL) ÷ volume × 1,000,000, the column Tread's UI shows. Negative means
the run made money. **Trading bps** = −trading PnL ÷ volume × 10,000, the part of the cost that is *not* exchange or
builder fees. It is the part that carries over to Arcus, where the maker fee is 0.

| Label | Rule (from the posted results) |
|---|---|
| **R1 low** | CPM ≤ $100, or trading PnL ≥ 0, and no run reported losing more than ~5% of its margin |
| **R2 moderate** | CPM $100–300, or the occasional stop-loss row, losses per day under ~10% of margin |
| **R3 high** | CPM $300–1,000, or 10–30% of the account lost within days, or frequent stop-losses |
| **R4 extreme** | CPM over $1,000, or runs that repeatedly hit their stop, or 30%+ of margin lost in a day |
| **U unproven** | Settings posted without results, or a single tiny run |

**Turnover** = volume ÷ margin (capital), per day or per hour. It is the capital-efficiency number the user asked
for, and only a few posts give both numbers.

## 1.3 The shortlist, ranked for the goal

Ranked by: capital efficiency first (where known), then cost, then how consistent the runs were.

| # | Setup (source) | Venue · asset | Volume and turnover | CPM (all-in) | Trading bps | Consistency | Risk |
|---|---|---|---|---|---|---|---|
| 1 | **Grid, "reasonable grid size"** (S20 @TomCryptoDefi, LIKELY) | RiseX, unspecified | $1.73M in 2 days on $1,650: **525× capital per day** (derived) | **$24** (derived from comp PnL −$40.80) | ~0.2 | Day 1: $1M on $1,500 "still positive in PnL" | **R1** |
| 2 | **DGrid +1/+2, 10–20x, calm hours, avoid NYC hours** (S02 @Zeus00nn, LIKELY) | RiseX · BTC | $800k in 3 days (tweet); 10 runs of $5–35k shown | **$67** (derived, 9 DGrid rows); $175 for the tweet's $140/800k | **−0.35** (trading profit) | 2 of 10 rows net profitable; rows $−53 to $213 | **R1** |
| 3 | **Mid −1, Aggressive, ~30% margin, max leverage** (S04 @H100Trader, LIKELY) | RiseX · BTC | $3.18M over 19 runs of $136–250k each | **$237** (derived); rows $121–369 | 1.37 | 0 of 19 profitable, but tight spread of outcomes; 1 stop-loss | **R2** (a reliable, known cost) |
| 4 | **Mid ±1 / Mid −3** (S03 @Ajay07157091 XAG; S29 @boldxbt XAU/US500/XAG, LIKELY) | Ondo · XAU, XAG, US500 | $319k (2 XAG rows); $428k (7 rows) | $226 (XAG); $398 (boldxbt) | 0.2–0.4 | Cost is almost all fees (2.1–3.6 bp); XAG hit a stop-loss: "be careful with XAG" | **R2** |
| 5 | **Mode hidden, stock perps** (S30 @boldxbt, LIKELY) | Hyperliquid xyz_ and Ondo · NVDA, INTC, SP500, GOOGL | $122k over 14 rows | **−$127** (profitable, derived) | −3.8 | 9 of 14 rows profitable; GOOGL rows cost $453–1,958 | **R2** |
| 6 | **Grid +10 to +20, $50–100 per bot, low leverage, several bots** (S15 @MWunsen, LIKELY) | Perpl · SOL | $50k, −$6 | $120 | n/a | Screenshot rows −$383 to +$255 | **R1–R2** (low volume per bot) |
| 7 | **Grid 7 bps, reset 1%, SL 25%** (S11 @NitroOGFX, LIKELY) | Hyperliquid · HYPE | $1.6M over 9 runs | $151 | −0.7 (gross +$114) | Chop runs +$60 to +$129; trend runs −$115 and −$183 | **R3** (trend risk) |
| 8 | **Mid −1 / −2 / −3** (S03 @Ajay07157091, LIKELY, referral) | Perpl · SOL | $788k all-time; **152× capital per day** (derived, ~7 days on ~$740) | $371 (8 rows); $394 portfolio | 3.25 | 0 of 8 profitable; **account −42% in a week**; win rate 20.9% | **R3** |
| 9 | **RGrid +2, 20x, Normal, TP reset 0.5%, TP 10%, SL 5%** (S06 @maxnoted, LIKELY) | Hyperliquid · CL (oil) | $48.5k over 4 runs (~$12k each) | **−$1,247** (profitable) | −14.9 | 4 of 4 profitable, but the profit is from catching a trend, not from spread | **R2–R3** (directional at 20x; low volume) |
| 10 | **Signal RSI +8, $100 margin, 10x** (S13 @davidyjeong, CONFIRMED) | Bybit · ETH | $19.9k in 1.4 h on $100: **142× capital per hour** | −$450 (profitable, net of $3.97 fees) | — | One round trip only | **U** (one trade) |
| 11 | **Grid +1** (S08 @0xhustler18, SPECULATIVE, referral) | Pacifica · ETH | $3.95k; "15–30 minutes to complete $10k" (tweet) | $230 | — | One partial run | **U** |
| 12 | **RGrid +1, Aggressive, TP 10%** (S01 @Asad30391661, LIKELY) | xyz · XYZ100 | $2.05k (21% filled) | −$537 | — | One partial run | **U** |

Aggregate data (TreadTools, not single runs), used to choose spreads rather than as entries:

| Source | What it says | Used for |
|---|---|---|
| S07 @OG_Branxi, Nado, $1.1B | Grid at 3–6 bps ≈ 0 bps PnL with the most volume; Grid at 0–1 bps loses "though they provide high completion rates"; Mid profitable only at 4–5 bps (+2.79 bps); RGrid best at 0–3 bps; avoid 23:00 UTC | Grid +3/+5 and Mid +4/+5 in the farm |
| S21 @tread_fi, all Grid, $7.34B | Median −0.5 bps; best hour 04:00 New York, worst 17:00 New York; "avoid market hours with grid bot during US equities market hours" | The skip-US-session variants |
| S35 @davidyjeong | "run +1 or more ... grid +0 will not allow the bot to collect spreads"; "the longer the bot is out there, the more risk" | Grid +1 as the tightest grid |

### Kept as failure cases (labelled, not discarded)

| Setup (source) | Result | Risk |
|---|---|---|
| Mid +5, Mid +20, Mid +50, Grid +10, Grid +20 on hyna_ BTC/ETH/SOL/FARTCOIN (S05/S46 @yxweb33) | Every run hit its stop (−$10 to −$11 each) on only $0.5–23k of volume: **CPM $810 to $25,550** (derived). CEO: "mid+10/50 isnt a winning strategy ... you probably caught a volatile period" | **R4** |
| DGrid on RiseX (S04 @H100Trader) | "some runs are profitable, while others end up at $1200+ CPM" | **R4 tail** of an otherwise R1–R2 mode |
| Perpl SOL Mid −3 (S03) | $22k, −$11.47, Stop Loss, $562/1M | **R3** |

### Settings posted without results (U, tested in the farm)

| Setup (source) | Settings |
|---|---|
| US500 Grid (S26 @AlphaWolfPRMR; S27 @aaalex) | Grid +1, 20x, reset 25% or 0.25%, SL 10% or 5%, Normal or Passive, "wait an hour after markets open" |
| US500 **Mid 0** (S28 @yieldyumi) | "Currently using mid 0". Tread team the same day: "Mid +2-10 coded" (S39) |
| Ondo Mid +1 Neutral (S34 @OG_Branxi) | Mid +1, Neutral |
| Grid +2 Normal on US500/US100 (S31 @katexbt) | Grid +2, Normal |
| RGrid Normal (S14 @_rose_pick) | TP reset 0.25%, SL 10%, TP uncapped |
| DGrid (S04, S12, S33 official and users) | No spread settings (auto); 10% TP, 10% SL, Normal duration; 10–20x; ≤ 60–70% of the margin |
| XAU DGrid at max leverage (S38 @pa95461271) | SPECULATIVE |

## 1.4 What the data says about the user's hypothesis (Mid 0, both sides)

The user's idea: limit orders on both sides at the mid, 0 bps, limit orders only.

- **It is the highest-volume Mid setting.** Nado's TreadTools data shows the tightest spreads have the highest
  completion rates (S07).
- **Every posted tight setting paid a trading loss on top of fees.** Mid −1 on RiseX BTC lost 1.37 bp per dollar
  before fees in 19 of 19 runs (S04). Mid −1..−3 on Perpl SOL lost 3.25 bp (S03). Nado Grid at 0–1 bps lost, and
  Mid was profitable only at 4–5 bps (S07). The CEO says Grid +0 "will not allow the bot to collect spreads" (S35).
- **No one posted a Mid 0 result** (S28 only names it).
- On Arcus the maker fee is 0, so a setup that loses ~1.4 bp before fees on Tread would cost ~$140 per $1M there.
  That is cheap for volume, but it is still a steady loss.

So the hypothesis is plausible for volume, but not for breakeven. It is tested head to head in the farm as `mid0`,
next to the wider settings the data favours.

## 1.5 How each kept setup maps onto the paper farm

The farm runs on one venue with maker (post-only) orders only, as the user asked. Its menu (31 settings) is in
`bot/bot/farm/menu.py`. Tread's modes map as follows:

| Tread mode | Farm setting(s) | Faithfulness |
|---|---|---|
| Mid 0 (user hypothesis; S28) | `mid0`: the tightest post-only bid and ask strictly around the mid | Exact for a post-only bot |
| Mid −1/−2/−3 (S03, S04) | Same as `mid0`. Two post-only quotes both past the mid would cross each other, so on one venue a negative Mid can only mean "as tight as possible". Tread probably crosses the spread (taker) on tight books. | Maker-only equivalent |
| Mid "join the touch" / "improve the touch" | `join`, `improve1` | Exact |
| Mid +1 … +5 (S07, S29, S34, S37) | `mid+1` … `mid+5`, plus `mid+1 skew` (inventory skew) | Exact |
| Grid +1 … +10, soft/grid reset 0.125–1% (S07, S08, S11, S15, S26, S27, S31, S35) | `grid+1 r0.25`, `grid+2 r0.5`, `grid+3 r0.5`, `grid+5 r0.5`, `grid+7 r1`, `grid+10 r1`: quotes around the **last fill**, soft reset at the threshold | Close: Tread's Grid anchors to the last fill (S09) |
| RGrid +1/+2/+3 (S01, S06, S14, S32) | `rgrid+1`, `rgrid+2`, `rgrid+3`: a trailing grid on an EMA of the mid that cuts losing inventory | **Approximation.** Tread's RGrid is "mostly taker" (S10); a maker-only version can only trail. |
| DGrid (S02, S04, S12, S33) | `dgrid`: picks Grid (chop) or RGrid (trend) from a 30-minute efficiency ratio, spacing from 1-minute volatility | **Approximation.** Tread's model is unpublished. |
| Signal RSI +3…+10 (S05, S13) | `rsi+3`, `rsi+5`, `rsi+8`: quotes skewed by RSI(14) on 1-minute prices (sell closer when RSI is high) | Close to the official description (S13) |
| "Avoid NYC hours" (S02, S21, S27) | `… skipUS` variants: no new quotes 09:00–16:30 New York time on NYSE days | Exact |
| (research addition) Mid 0 only when calm (S02 "stable market", S07 "Mid 4–5 bps when volatile") | `mid0 vgate`: Mid 0 while 0.25 × the 1-minute volatility is under 0.5 bp and there is no trend, else up to 3 bps | New |
| (research addition) spread scaled to volatility (S11 "scale spread with ATR") | `mid vadapt`: mid ± 0.5 × the 1-minute volatility, 0–5 bps, 5 bps in a trend | New |

Sizing follows the posts: capital $100, leverage 5x, 10x and 20x (capped at the market's maximum), a position stop
of 5% of capital, a daily stop of 10% and a kill at 20%. Tread users run SL 5–25% of margin (S06, S11, S14, S26, S27).

## 1.6 Live verdict (2026-09-25 22:17 to 09-26 07:13 UTC, Arcus, 20x, 11 liquid markets)

From [REPORT.md](REPORT.md) section 6. The same setups, measured on live Arcus data (no fees for makers, so these
costs are the trading part only). The verdict updates each setup's risk label.

| Tread setup | Farm setting | Live: turnover / h, cost per $1M, profitable markets | Verdict |
|---|---|---|---|
| Mid 0 (S28), Mid −1/−3 (S03, S04) | `mid0` | 59×, $62, 2 of 11 | Most volume, steady cost: **R3** |
| Mid −1 aggressive (S04) | `improve1`, `touch 0bp` | 41–48×, $85–115, 3–4 of 11 | **R3**; at max leverage the daily stop fires within hours |
| Mid +1 (S34, S29) | `mid+1`, `mid+1 skew` | 24–34×, −$12 to $53, 4 of 11 | Near breakeven: **R2** |
| Mid +3…+5 (S07) | `mid+3`, `mid+5` | 5×, −$117 (+3); 2×, $896 (+5) | +3 **R1** at low volume; +5 too few fills |
| Grid +1…+3, soft reset (S07, S27, S31, S35) | `grid+1 r0.25`, `grid+3 r0.5` | 13–26×, −$20 to −$28, 6–8 of 11 | Best non-Mid family: **R2** |
| Grid +7…+10 (S11, S15) | `grid+7 r1`, `grid+10 r1` | 4×, $95 | Too few fills for this goal |
| RGrid +1/+2 (S01, S06, S32) | `rgrid+1 r0.25`, `rgrid+2 r0.5` | 28–30×, $151–176, 2–3 of 11 | **R3** (maker approximation) |
| DGrid (S02, S04, S12, S33) | `dgrid` | 27×, $14, 4 of 11 | Near breakeven; approximation: **R2–R3** |
| Signal RSI +3…+8 (S05, S13) | `rsi+3`, `rsi+8` | 4–10×, −$190 to −$1 | Profitable but low volume: **R2** |
| (new) Mid 0 when calm | `mid0 vgate` | 36×, $20, 2 of 11 | A third of Mid 0's cost: **R3** until proven |
