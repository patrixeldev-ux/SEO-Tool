# Methodology

How Search OS arrives at its numbers, in plain language. Where a number is a guess rather than a measurement, this
page says so, and so does the app.

## Action priority

The planner (Phase 3) turns audit findings and Search Console opportunities into **actions**. Each action carries
three numbers: an **expected gain**, a **confidence** and an **effort**. They are combined into one **priority**,
which is only used to sort the plan board.

    priority = expected_gain x prior_win_rate(type) / effort

* **Expected gain** is the estimated number of extra clicks per 28 days if the action is done.
* **prior_win_rate** is 0.5 for every action type. It is the chance the change helps at all, and for now it is a
  placeholder: Phase 5 (experiments) replaces it with win rates measured on real changes.
* **Effort** is a fixed whole number from 1 (a minute) to 5 (days of work) for each action type, listed below.

The priority is a sorting aid, not a forecast. Do not add priorities up or read them as clicks.

### Where the expected gain comes from

There are two kinds of estimate. The app always shows which one an action uses (`gain_basis` in the action's
evidence: "gsc" or "prior").

**1. Measured inputs ("gsc").** Used when Search Console data for the page exists.

* *Title and description rewrites from a CTR gap.* The site's own click-through-rate curve (CTR by position, fitted
  from its Search Console data; see `backend/searchos/stats/ctr_curve.py`) says what CTR a page ranking at a
  given position normally gets. A query whose real CTR is clearly below that (its 95% Wilson interval's upper end
  is below the curve) has a gap:

      gap = impressions_28d x (curve_ctr(position) - actual_ctr)

  A page's title and its description both shape the same search snippet, so the gap is shared between the two
  actions: **60% to the title, 40% to the description**. Together they never claim more than the gap itself.
* *Internal links to a "striking distance" page.* A page ranking just below the first results is assumed to move up
  **2 positions** once it is better linked (a prior). The gain is
  `impressions_28d x (curve_ctr(position - 2) - baseline)`, where the baseline is the larger of the page's actual
  CTR and the curve's CTR at its current position (so a page that already beats the curve is not credited twice).

**2. Priors ("prior, not measured").** Used when nothing can be measured: a missing title on a page with no search
data, a broken canonical, a redirect chain. These are **our own guesses**, deliberately small, and shown in the app
with the label "prior, not measured". A page that already gets clicks can have a larger prior: the larger of the
flat number and a small share of its last-28-day clicks (the "share" column). They exist only so that the plan has
a sensible order before there is evidence; they will be replaced by measured effects from experiments.

| Action type | Flat prior (extra clicks / 28 days) | Share of the page's clicks |
| --- | --- | --- |
| rewrite_title | 3.0 | 5% |
| rewrite_meta | 2.0 | 3% |
| add_alt | 0.5 | 0.2% |
| add_jsonld | 4.0 | 3% |
| fix_jsonld | 2.0 | 3% |
| add_internal_links | 2.0 | 3% |
| fix_canonical | 4.0 | 5% |
| collapse_redirect | 1.0 | - |
| fix_link | 1.0 | - |
| refresh_content | 6.0 | 10% |
| consolidate_pages | 8.0 | - |
| ssr_task | 10.0 | - |
| robots_review | 6.0 | - |
| noindex_review | 2.0 | - |
| author_eeat | 3.0 | - |
| earn_links | 5.0 | - |
| collect_reviews | 2.0 | - |
| digital_pr | 4.0 | - |

`dev_fix` (a technical fix for a developer) covers many different audit checks, so its prior depends on the
check's severity: critical 4.0, high 2.5, medium 1.0, low 0.5, info 0.0.

### Confidence

Confidence is a number from 0 to 0.95 saying how much to trust the *gain estimate*, not whether the action is a
good idea.

* A prior-only estimate always has confidence **0.2**.
* A measured estimate has `volume x curve factor`. `volume = impressions / (impressions + 500)`: 500 impressions
  gives 0.5, 5,000 gives 0.91. The curve factor is **1.0** when the CTR curve rests on the site's own data at every
  position, **0.7** when part of it is borrowed from other sites of the organisation, and **0.5** when it falls
  back on the industry default. The result is capped at 0.95.

### Effort and risk class

Both are fixed per action type (in `backend/searchos/planner/catalog.py`; a test keeps this page and the code in
step). The risk class decides what may ever happen without a person:

* **auto**: low-risk text and markup. When a site's autopilot switch is ON (Phase 4), these may be deployed
  without approval, with a verification step and automatic rollback.
* **approve**: always needs a person's approval first.
* **manual**: never automated; the app explains what to do and tracks it as a task.

| Action type | Risk class | Effort (1-5) |
| --- | --- | --- |
| rewrite_title | auto | 1 |
| rewrite_meta | auto | 1 |
| add_alt | auto | 1 |
| add_jsonld | auto | 2 |
| fix_jsonld | auto | 2 |
| add_internal_links | auto | 2 |
| fix_canonical | approve | 2 |
| collapse_redirect | approve | 2 |
| fix_link | approve | 1 |
| refresh_content | approve | 4 |
| consolidate_pages | approve | 5 |
| ssr_task | manual | 5 |
| robots_review | manual | 1 |
| noindex_review | manual | 1 |
| dev_fix | manual | 3 |
| author_eeat | manual | 3 |
| earn_links | manual | 5 |
| collect_reviews | manual | 3 |
| digital_pr | manual | 5 |

**Never automated, whatever the risk class says:** deleting a page, changes to `robots.txt` rules, changes to
`noindex`, and link exchanges or paid links. This list is enforced in code and by a test.

### Manual tasks

Four kinds of work cannot be automated and are never offered as one-click changes: **earning links and mentions**,
**showing who is behind the content (author and E-E-A-T)**, **collecting genuine reviews** and **digital PR**.
Each appears as one checklist per site, written from the site's own data (the pages most worth citing, the
queries people already find the site by, whether an About page exists, which articles show no author). Their
expected gain is a prior ("prior, not measured") like any other action without data. Every checklist also lists
what not to do: buy or swap links, mass-email strangers, write or buy reviews, invent credentials, publish
made-up statistics. Ticking items off is saved; "Mark done" closes the task.

### Rejected actions come back only for a reason

An action a person rejects is not proposed again for **90 days**, unless its priority has risen to at least **1.5
times** what it was when it was rejected (for example because the page's impressions grew). A finding that has
disappeared from the latest audit closes its open action as "obsolete".

### Internal-link suggestions: the similarity threshold

Pages are compared by the words they use: TF-IDF vectors (term frequency `1 + ln(tf)`, inverse document frequency
`ln((1 + N) / (1 + df)) + 1`, unit length) and cosine similarity. This runs on the site's own pages, locally and
deterministically; no AI and no network. A page is suggested as a link *source* for a target only when the cosine
is at least **0.05**.

That threshold was calibrated, not guessed, on a synthetic set of eight short pages on two unrelated topics (coffee
and web hosting; `backend/tests/data/topical_pages.py`): every cross-topic pair scored below 0.032 and the median
same-topic pair scored about 0.085, with half of all same-topic pairs above 0.05. So 0.05 never links across the
two topics and still keeps the closer half of related pages. It is deliberately on the cautious side: a missing
suggestion costs little; a bad link costs trust. A test re-checks this separation on every run. Real sites differ;
if suggestions look too sparse or too loose on yours, the constant is `SIMILARITY_THRESHOLD` in
`backend/searchos/planner/similarity.py`.

On top of similarity, a suggested link must come from a page that is indexable and answers 200, must not already
link to the target, and its anchor text must be a phrase that already appears in the source page's text. At most 3
links are proposed per source page and 3 source pages per target.

### What the AI may and may not do

Titles, descriptions, alt text and JSON-LD are written by an AI model (Anthropic Claude or Google Gemini, chosen in
`.env`). Nothing it writes is trusted: every answer must pass fixed, non-AI checks (length limits, uniqueness,
no markup, JSON-LD matched against the required-properties table, no invented ratings or reviews, existing markup
kept). An answer that fails is sent back with the reasons, at most twice; after that the suggestion is dropped and
the reason is shown. Page text is passed to the model as clearly delimited data, at most 3,000 characters, with an
instruction that it is data, never instructions. Without an AI key the plan still lists every action; the ones that
need written text show "needs AI".
