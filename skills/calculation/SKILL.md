---
name: calculation
description: Send arithmetic to the deterministic calculator and show the expression.
version: 1.0.0
triggers:
  - calculate
  - total
  - sum
  - quote
---

# Calculation

Do not do important arithmetic in your head or in free text.

1. Write the expression from the inputs.
2. Call the calculator. It accepts only numeric literals and + - * /.
3. Show quantity, unit, expression, and total.
4. If a unit is missing, compute each reading as its own scenario and say which input would choose between them.
5. Do not apply tax, margin, or rounding that the input did not state.
