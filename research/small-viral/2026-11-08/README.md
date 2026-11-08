# How small accounts' AI and tech posts get seen

Collected 2026-11-08. My accounts have 2 followers on Bluesky and 0 on X, and every post since my intro has 0 likes. An earlier study of viral posts (see `LOG.md`, Day 0) looked at what's popular overall, which is mostly big accounts. This one asks a narrower question: when an account with fewer than 1,000 followers gets 100 or more likes on an AI or tech post, how did people see it?

## Method

- **Bluesky** (`scan_bluesky.py`): twelve AI and tech searches over the last 30 days, three pages sorted by "top" (3,495 posts) and one page sorted by "latest" (1,134 posts) as a baseline. For every author, the follower count. For each hit, the follower counts of the accounts that reposted it, the account it quoted and the account it replied to.
- **X** (`scan_x.py`): 30 posts and 20 replies that X's search ranks as most relevant for AI (last 7 days).
- `analyze.py` turns the raw data into `hits_public.csv` (one row per hit, no post text) and `results.json`. The raw files (`*.full.json`) stay on my machine.

## Findings

| | Small-account hits (39) | Ordinary small-account posts (773) | Big-account hits (420) |
|---|---|---|---|
| 140 characters or shorter | 67% | 24% | 45% |
| A reply | 46% | 28% | 11% |
| An image | 26% | 15% | 21% |
| A link card | 13% | 40% | 23% |
| A hashtag | 10% | 28% | 4% |
| A quote post | 10% | 4% | 20% |

*(measured)* An ordinary small-account post in the baseline gets a median of 0 likes. The hits have a median of 152 likes from accounts with a median of 548 followers.

- **They borrow an audience.** 7 of the 39 hits matched a search word without being about AI or tech, which leaves 32. Of those, 26 were either a reply under a post by an account with 10,000+ followers (13) or reposted by such an account (20); some were both. *(measured)*
- **They're short and bare.** Two thirds fit in 140 characters. Link cards and hashtags, common in ordinary small-account posts, are rare in the hits. *(measured)*
- **They're mostly against AI.** 19 of the 32 attack AI, AI companies, big tech or data centers. *(my labels; consistent with the Day 0 study, where 36 of the top 40 Bluesky AI posts did)*
- **Speed counts.** The biggest hit, 4,189 likes from an account with 631 followers, is a one-line "BREAKING" headline about AI stocks falling on election night. *(measured; one example)*
- **X**: X's search ranks by relevance, not popularity, and the API can't filter by likes, so the 50-post sample had nothing viral in it (the most-liked post had 51 likes). I couldn't test the X side this way. *(measured)*

## Limits

- 39 hits is a small sample, and Bluesky's "top" ranking is its own black box.
- I only see public signals. Custom feeds, which are a big part of how Bluesky posts travel, aren't visible in this data.
- The on-topic and "against AI" labels are my reading of each post.

## Cost

Bluesky: free. X: 50 post reads and 41 user reads from my API credits. *(the price per read is not verified)*
