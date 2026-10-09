# Ayven delivery work — 9 October 2026

This is an engine implementation checkpoint, not a completed product or a live-model qualification.

## Implemented

- General live jobs obtain a request-specific, validated plan: requested output and up to eight required items. The plan cannot authorise external actions or introduce arbitrary tools.
- Supplied-information writing can skip web research; jobs needing current external information retain search and opened-page evidence.
- The employee's checked text becomes the deliverable, rather than leaving the original generic placeholder as the published child result.
- A separate requirement-by-requirement review checks the actual output. Missing, duplicate or invalid review items fail closed. One correction attempt is permitted; a still-incomplete result is failed rather than completed or promoted by approval.
- Review judgements are explicitly model reviews, not independent proof of truth. Existing grounding and safety checks remain in place.
- Door completion checks use the current quote's calculated amounts, labour unit and VAT treatment. They no longer expect an old example's prices. Explicit labour units are displayed as explicit.
- General research with no usable evidence fails; collected sources alone are partial progress.
- Native local Ollama mode requests no thinking output, retains the model for fifteen minutes, rejects cloud/remote model endpoints and records model timing. These changes request lower latency; performance on Lee's PC is unmeasured.

## Verification

79 focused planning/review/completion/provider/intelligence/recovery/bridge tests passed on the final engine changes. Controlled generator responses and mocked provider requests are used for the new planning paths. No live Qwen result is claimed. Existing process cleanup warnings remain in the older browser-related dependencies.

## Still required

Real-model acceptance on the installed Windows runtime; wider arbitrary-job coverage; stronger evidence-entailment verification; clean result presentation and downloadable documents; natural Milo delegation/results; startup and update installation; approved-action connectors; persistent company profile selection; cloud delivery later.

The role audit now uses the current quote and calculator instead of old example totals. The new general planning path does not replace specialist door/ticket/vending paths yet.
