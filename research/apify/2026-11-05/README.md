# What happens to new tools on the Apify Store (2026-11-05)

## Question

Before building anything to sell, how do brand-new listings on an AI-friendly marketplace actually do?

## Result

Of 1,284 sampled tools created in the last 14 days:

| Users so far (past 30 days) | Tools | Share |
|---|---|---|
| 0 | 85 | 6.6% |
| 1 | 1,128 | 87.9% |
| 2–9 | 70 | 5.5% |
| 10 or more | 1 | 0.1% |

- **94% have had 0 or 1 users** (95% confidence interval ±1.25 percentage points).
- **At least 11,000 new tools were created in those 14 days.** 85.6% of the sample was created in that window, and the newest listing holds 13,390 tools: 0.856 × 13,390 ≈ 11,462. It is a floor, because anything older than the reachable listing is invisible.
- My guess is that the single user is usually the creator testing the tool. That part is inference, not measured.

## Method

1. `collect.py` pages through the public store endpoint (`/v2/store?sortBy=newest`) until it returns no items: 13,390 unique tools.
2. It draws a random sample of 1,500 (seed 11) and looks up each one's exact `createdAt` via `/v2/acts/{username}~{name}`.
3. `make_card.py` computes every number on the card from `recent_sample.csv`. Nothing is typed in by hand.

Raw console output is in `collect_output.txt`.

## Limits (read these before quoting the numbers)

- **The API only pages about 15,000 positions deep**, for any sort order. Past that it returns empty pages. The whole store (100,000+ tools) cannot be listed in one query, so this study covers recent tools only.
- **Each page returns fewer items than requested** (800–980 instead of 1,000), and the `count` field does not reflect that. An earlier version of my script stopped early and silently covered only 11% of what it claimed. It now fails loudly instead.
- **The "newest" sort is not strictly by creation date.** Old tools that publish a new version also appear near the top, which is why the age of every sampled tool is looked up individually. Older age groups in this sample are therefore biased toward recently updated tools and are not reported.
- "Users" is Apify's `totalUsers30Days`, which counts distinct users of one tool in the past 30 days.

## Reproduce

```bash
python3 collect.py .      # about 10 minutes, public API only, no account needed
python3 make_card.py      # needs Google Chrome for the PNG
```
