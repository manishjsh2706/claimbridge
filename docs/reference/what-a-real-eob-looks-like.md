# What a real Explanation of Benefits actually looks like

Notes taken from the sample EOB published by CMS (the US agency that runs
Medicare), checked on 2026-09-28. This is a summary and a set of quotations for
reference, not a copy of the document; the source is linked at the bottom and is
public domain.

Written down because the project's own framing was getting sloppy in
conversation, and an inaccurate claim about EOBs is the kind of thing that gets
caught in an interview by anyone who has worked in US healthcare.

## What the CMS sample contains

Two pages. The claims table uses these column headings, verbatim:

    Line No. | Service Description | Date of Service | Claim Status |
    Provider Charges | Allowed Charges | Co Pay | Deductible |
    Coinsurance | Paid by Insurer | Remark Code

Remark codes are explained in a legend. For example:

> "PDC — Billed amount is higher than the maximum payment insurance allows.
> The payment is for the allowed amount."

It does give the member guidance, in general terms: pay your bills and keep the
paperwork, you "may be able to appeal" a coverage decision you disagree with,
you may receive a separate bill from the provider, and call the customer service
number with questions.

## What this means for ClaimBridge

**A real EOB is not unreadable, and saying so is wrong.** Column headings are in
English, codes have a legend, and generic next steps are printed. Any claim that
members get "a cryptic line of codes" is an exaggeration that will not survive
a question from someone who knows the domain.

The accurate statement is narrower, and still true:

> An EOB is a **statement**, not an **explanation**. It answers "what are the
> numbers" well. It does not answer "why did this happen on *my* claim", "which
> rule of *my* plan applied", or "what exactly do I do next, and by when".

Concretely, the gap this project fills:

| A real EOB gives | A member needs |
|------------------|----------------|
| "PDC — billed amount is higher than allowed" | "Your provider billed $285. Your plan allows $165 for this service, so $33 is yours to pay." |
| "you may be able to appeal" | "You can appeal within 180 days. Call 1-800-555-0142." |
| A table, identical in shape for every claim | A few sentences citing the section of *this* plan's policy that applied |

The last row is the one that is hard, and the reason the system retrieves from
each tenant's own policy documents rather than from a generic template.

## The two PDFs in this folder

**`cms-11819-reading-your-eob.pdf`** — the CMS publication itself, text and all,
re-set as a readable two-page PDF. The words are transcribed from CMS
Publication #11819 (Revision Date May 2022), a US Department of Health and Human
Services publication and therefore public domain; the layout is re-set, so this
reproduces the text faithfully rather than copying the page design. It carries a
provenance box saying exactly that. It exists because the original link is often
unreachable from outside the US.

It includes the real example data from that publication: two medical-care lines
totalling $406.60 billed, $120.27 allowed, $85.27 paid by the insurer and $35.00
owed, with remark code PDC, plus all eight numbered explanations and the
"Pay your bills" and "Appeals" sections.

**`sample-eob-pacific-hmo.pdf`** — the same layout with this project's own
CLAIM-PH-001 data.

One detail the full transcription corrected: the real column order is
`... Paid by Insurer | What You Owe | Remark Code`. The first draft of the
Pacific sample had Remark Code before What You Owe, which is wrong, and is now
fixed. Worth noting as a small lesson -- the first fetch gave a summary, and the
summary listed the columns in a different order than the document does.

### About the Pacific sample

`sample-eob-pacific-hmo.pdf` is a one-page EOB built for the demo. Its layout
follows the CMS sample -- the header block, the twelve claim-line columns ending
in "What You Owe", the remark-code legend, the numbered reference guide and the
appeals footer -- while the data is this project's own CLAIM-PH-001 fixture:
Suresh Menon, Sound Orthopedics, office visit, $285 billed, $165 allowed, $132
paid, $33 owed, CO-45.

It carries a banner saying it is illustrative and not a real insurer document,
and it should keep that banner. Pacific HMO is fictional and so is everyone on
the page; a realistic-looking EOB without that line is a forgeable record, not a
teaching aid.

It exists because the CMS link is often unreachable from outside the US, and
because putting the real input beside the system's output makes the argument in
one screen: this page is what the member gets today, and the reviewer console
shows what ClaimBridge produces from the same claim.

## Sources

- CMS, "Reading Your Explanation of Benefits" (sample PDF):
  https://www.cms.gov/files/document/11819-sample-explanation-benefits-508.pdf
- Blue Shield of California, "How to Read an EOB":
  https://www.blueshieldca.com/en/home/help-and-support/how-to-read-eob
- UnitedHealthcare, "Explanation of Benefits":
  https://www.uhc.com/understanding-health-insurance/how-does-health-insurance-work/explanation-of-benefits
- University of Utah Health, "EOB meaning and example statement":
  https://healthcare.utah.edu/bill/eob-explanation-benefits

The CMS link may be unreachable from outside the US; the other three are
ordinary company and university pages and generally are not.
