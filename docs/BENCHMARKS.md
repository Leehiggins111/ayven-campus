# Benchmarks

The exam is frozen in `benchmarks/exams/` and in `app.intelligence.assertions`. Do not weaken the strings to make a run pass.

## A. Trades

7 internal doors, supplied and fitted, Livingston. Sizes 762x1981, 762x1981, 686x1981, 762x1981, 838x1981, 762x1981, 686x1981. Oak-looking, black handles. Provisional inputs: door £82, handle £18, hinges £7, labour £95, delivery £35/job, consumables £12/door.

Local deterministic result (fixture research, stub models):

- Labour stays AMBIGUOUS. Both scenarios are shown. Neither is selected.
- Per-door labour, ex VAT: £1533.00. The £214.00 figure is per door excluding delivery, inside that scenario only.
- Per-job labour, ex VAT: £963.00.
- VAT is not applied.
- Hinge count is an assumption, labelled not proven.
- No supplier is named, because no supplier page was retrieved.
- Missing fields include handing, thickness, frame, hardware, measurements, and VAT.
- Approval is required. Nothing is sent.
- This is not a final quote.

## B. Football

Borussia Dortmund, Ajax, Sparta Prague, Rosenborg.

Local fixture result, excerpts captured 2026-09-25 and labelled `FIXTURE_SNAPSHOT`:

- Dortmund: `https://www.bvb.de/de/de/tickets.html` (official ticket shop text). Not live stock.
- Ajax: `https://www.ajax.nl/fans/kaartverkoop/` including the club's warning about zwarthandel (resale on unofficial channels).
- Sparta: `https://sparta.cz/cs` official site. No ticket inventory in the retrieved text.
- Rosenborg: `https://www.rbk.no/billetter`. Sale wording in the snapshot is not treated as current availability.
- No reseller is called an authorised partner. No purchase. No invented URL.

A live GPU run with `AYVEN_RESEARCH_MODE=live` must open pages again. Fixture text is not a substitute for that run.

## C. Vending

Local fixture result:

- Contracts Finder and the Companies House register were opened as public pages.
- Neither page named a placement prospect. Prospects: none evidenced.
- No statistic is recorded.
- Outreach is `DRAFT_ONLY`, with no recipient. Sent: no. Approval required.

## Assertions

`evaluate_project` checks the trades totals and ambiguity, football URLs and channels, vending non-contact, and for every exam: no think tags, claims stored, sources or recorded failures, a supervisor audit, a manager payload that includes those audits, no frontier backend, and a pending approval.

Local `evaluate_project` on fixture pages: A 19/19, B 18/18, C 13/13. The suite at this version is 18 passed. These numbers do **not** mean a Qwen model passed the exam.

## Frontier comparison

`benchmarks/comparison/` holds:

- `baseline/` — qualitative v0.4 failure notes. The numeric GPU artefacts were lost with the pod.
- `ayven/` — drop a saved run here for a human to read.
- `reference/` — a human pastes a frontier answer. This repo does not call that API.
- `scores/` — the blind rubric: correctness, completeness, evidence, usefulness, clarity, hallucinations, actionability.

Question for the human: would you accept the work if you thought a frontier assistant had written it?
