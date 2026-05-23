# Snapshot 2026-05-23 13:49 PST — first end-to-end PCA run

## State at snapshot time

| Metric | Value |
|---|---|
| Phase 3 (fetch) progress | ~55,000 / 181,326 pages (30%) |
| Phase 4 (embed) progress | 10,214 embeddings |
| PCA corpus (n_bookmarks ≥ 3) | 10,051 eligible pages; **6,759 had embeddings**, fit on those |
| PCA components | 10 (24.17% cumulative explained variance) |
| Cohort bookmark coverage | Harrison 50/203 · Eva 58/126 · Danoli2 73/758 |

Important caveats:
- The fitted PCA is on the **popular subset** of the corpus that had already been
  embedded by the popularity-first run. Once Phase 4 catches up to all 10,051
  ≥3-bookmark pages, the axes will shift slightly.
- Per-user means are biased toward the population centroid because the embedded
  bookmark subset is also popularity-first (the long tail of idiosyncratic picks
  isn't represented yet).

## How the 10 axes hang together

The axes describe a *specific* reader population — Bay Area-adjacent tech-
intellectual web readers — not a generic taste space.

- Roughly half the axes (PC1, 4, 5, 6, 8) are variants of "personal/emotional
  vs analytical/technical."
- The other half slice up the *technical* side into sub-tribes:
  alignment-rationalism (PC2), startup-VC (PC3), design-craft (PC2/PC10),
  frontier-tech (PC9), productivity-tools (PC10).
- Notably *absent* from the variance: politics, sports, news, business news,
  fiction. Confirms Curius is a niche platform.

### Per-axis read

- **PC1 — personal_essay_vs_ai_tech_product** (6.2% var). Inner life vs technical
  work. The fundamental split.
- **PC2 — ai_alignment_discourse_vs_web_design_craft**. Two whole subcultures:
  rationalist long-form vs personal-portfolio/creative-coding/indie-web.
- **PC3 — startup_venture_capital_ness**. Negative pole is *poetry + ML
  interpretability* — i.e., reading for love of the thing, not for career.
  Not "non-startup"; "anti-instrumental."
- **PC4 — cultural_critique_vs_self_improvement**. Grand cultural narratives
  vs practical productivity ("how to do great work"). High-status worldbuilding
  vs low-status how-to.
- **PC10 — design_taste_and_craft_discourse**. Aesthetic taste-as-essay vs
  PKM/note-taking systems. Designers vs tool-collectors.

24% cumulative over 10 PCs is normal for 4096-dim embedding clouds. Remaining
76% lives in higher PCs — each axis here is a narrow slice, not a comprehensive
taste theory.

## Where the cohort users sit

Threshold for "real signal": |mean| > 0.05. |mean| < 0.02 is noise.

### Danoli2 (10% coverage but strongest signature)

- **−0.11 on alignment vs design-craft** → strongly creative-coding/aesthetic
- **−0.09 on personal-essay vs AI-product** → reads tech-product pages, not introspection
- **+0.07 on personal-bio vs essay** → collects people's about-me pages
- **+0.06 on startup-ness** → startup-aware
- **+0.05 on intellectual-self-improvement vs relational-emotional**

Read: a maker/shipper/admirer-of-people reader. Anti-doom, anti-introspection,
pro-craft.

### Eva L

- **−0.07 on quant-business vs AI/HCI reflection** → solidly on HCI/creativity/
  reflective AI side, not business-quant
- Mild −0.04 on aesthetics-vs-systems (prefers systems)

Her personality blurb mentioned "AI interpretability, hardware, outreach" —
interpretability registers in this tilt; hardware doesn't (no hardware axis
exists in our 10).

### Harrison Qian

Remarkably centrist:
- **−0.06 on cultural-critique vs self-improvement** → leans practical
  productivity over grand cultural commentary
- Slight +0.03 on alignment-discourse, −0.03 on design-craft
- Everything else essentially at the centroid

Most likely: genuinely reads across the centroid (matches "wide curiosity").
Caveat: 50/203 bookmarks embedded, all popularity-first → mainstream → centroid-
biased. Picture will sharpen once idiosyncratic picks land in Phase 4.

### Cohort-level pattern

All three are **negative** on `cultural_critique_vs_self_improvement` *and*
**negative** on `quantitative_business_analysis_vs_ai_creativity_reflection`.
Collectively the cohort leans away from grand cultural narratives and away
from quant-business, toward practical productivity + reflective AI/HCI/
creativity. Coherent subculture position even though it's only three people.

## How to reproduce / extend

This snapshot is read-only. To regenerate the live `axes/` and `profiles/`
directories from this snapshot, copy the files back:

```bash
cp snapshots/2026-05-23-1349/axes/* axes/
cp snapshots/2026-05-23-1349/profiles/* profiles/
```

To run a fresh PCA + naming + profile build on the latest (larger) embedding
set, just re-run the phases (will overwrite live `axes/` and `profiles/`):

```bash
uv run python -m links_to_profile.pca
uv run python -m links_to_profile.name_axes
uv run python -m links_to_profile.build_profiles
```

When taking the next snapshot, use a new dated directory under `snapshots/`.
