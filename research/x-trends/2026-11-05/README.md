# What gets attention in "AI agents + money" posts on X

Collected 2026-11-05. Question: in the posts X's search ranks highest for AI agents and money, what gets attention, and do the money claims come with anything you can check?

## Results

| Measure | Result |
|---|---|
| Posts | 99: 4 queries × 25, X's relevance order, posted Oct 30 – Nov 6, 2026 (UTC) |
| Mentioned a dollar figure | 22 |
| … and linked anywhere | 4 |
| … and linked to a way to check it | 0 |
| Engagement bait, all 99 posts | 13 |
| Top 20 with replies above 5% of views | 8. The median for posts with 100+ views is 0.79% (52 posts) |
| Top 20 mentioning an account that 3+ different authors mentioned | 5 |
| Top 20 asking the X algorithm for reach | 3 |
| Top 20 with none of those signs | 9, of which 3 I labeled as the same joke format |

The most-liked post in the sample (155 likes) is one of those jokes: someone gives an agent money or access, and it does something absurd.

## Method

- `scan.py` runs four X API recent-search queries (listed in `results.json`), 25 posts each, sorted by X's relevance, English only, no reposts or replies.
- `analyze.py` tags every post with fixed rules: a dollar-figure regex; "a way to check" = a link to a block explorer, a revenue verifier or code (the domain list is in `results.json`); engagement bait = asking the X algorithm, "let's connect", comment-to-DM and similar phrases; promotion = mentioning an account that 3 or more different authors in the sample mentioned (the platform's own @X excluded); reply spike = replies above 5% of views on posts with 100+ views.
- `manual_labels.csv` holds my hand labels for the 9 top-20 posts that no rule tagged.
- `make_card.py` builds the stat card from those files, so the numbers can't drift.

## Limits

- This is X's relevance ranking for four queries, not a sample of all posts.
- A reply spike is a signal, not proof of reply pods.
- "A way to check" is a narrow fixed list. A screenshot doesn't count, and proof posted in a reply isn't captured.
- The manual labels are one reader's judgment: mine.
- Post texts are not published, because X's developer terms don't allow redistributing them. Post IDs are, in `posts_public.csv`, so anyone with API access can fetch the same posts.
- Cost: 99 post reads from my X API credits.

## Reproduce

```
python3 scan.py      # needs X API credentials; about 100 post reads
python3 analyze.py
python3 make_card.py
```
