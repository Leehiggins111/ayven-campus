---
name: internal-door-quoting
description: Prepare provisional internal-door scenarios and list every missing specification before a quote.
version: 1.0.0
triggers:
  - internal door
  - hinges
  - labour
tools:
  - calculator
  - web_search
  - fetch_page
evidence:
  - opened supplier page for any named supplier
  - calculator totals for both labour readings
checks:
  - labour unit stays ambiguous unless the input says per door or per job
  - VAT stays unknown unless the input states it
permissions:
  - READ_WEB
  - RUN_CALC
---

# Internal door quoting

This skill teaches the quoting process. It does not contain a price for a customer.

## Units

Treat a component price as per door only when the input says so, or when it is clearly a unit price for that component (door, handle, consumables stated per door).

Labour is different. "Labour £95" with no "per door" or "per job" is AMBIGUOUS. Compute both scenarios. Do not choose one. Do not call either total a quote.

Delivery stated "/job" is once per job. Consumables stated "/door" are per door.

A hinge price with no count is not proven. You may show an assumption of one charge per door, labelled ASSUMED_PER_DOOR_NOT_PROVEN. Say that hinge positions and hinge count are unknown.

## VAT

If the input does not say inclusive, exclusive, or a rate, do not apply VAT. Say the rate is unknown.

## What a fitter still needs

Before any customer total, identify what is missing:

- leaf size versus structural opening
- wall or lining thickness
- frame new or existing, and whether the frame is in the door price
- handing and opening direction for each leaf
- fire rating
- what "oak-looking" means
- handle type, lock, and finish
- hinge count, size, finish, and positions
- whether labour is supply-and-fit, and per door or per job
- architraves, threshold, making good, waste
- site access and floor in the named town
- who unloads delivery
- VAT treatment
- customer confirmation of the provisional prices
- survey and lead time

Different widths are not evidence that every leaf has the same trade price.

## Publication

Show both totals, the expressions, the missing fields, and the sentence that this is not a final quote. Do not publish £214 per door or a single job total as the answer unless the labour unit and the other fields above are actually known.
