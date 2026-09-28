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
