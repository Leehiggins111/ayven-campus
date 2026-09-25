"""Internal-door scenarios. Ambiguous labour is preserved, never resolved."""

from __future__ import annotations

import re
from decimal import Decimal

from .calc import eval_arithmetic, money, sum_pence, to_pence

MISSING_FIELDS = [
    "Whether each size is the door leaf or the structural opening",
    "Wall or lining thickness",
    "Frame: existing or new, and whether a frame is included in the door price",
    "Handing and opening direction for each leaf",
    "Fire rating, if any",
    "What oak-looking means: veneer, laminate, or paint",
    "Handle type, lock function, and finish beyond black",
    "Hinge count, size, finish, and positions — £7 is not a proven per-door or per-hinge price",
    "Whether labour of £95 is per door or per job, and what fitting includes",
    "Architraves, threshold, making good, and waste removal",
    "Site access, floor, and parking in Livingston",
    "Delivery unloading and who receives it",
    "VAT treatment — not stated, so not applied",
    "Customer confirmation that the provisional prices are the prices to use",
    "Survey or lead time",
]

_SIZE = re.compile(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", re.I)
_PRICE = {
    "door": re.compile(r"\bdoors?\s*£\s*([\d,]+(?:\.\d+)?)", re.I),
    "handle": re.compile(r"\bhandles?\s*£\s*([\d,]+(?:\.\d+)?)", re.I),
    "hinges": re.compile(r"\bhinges?\s*£\s*([\d,]+(?:\.\d+)?)", re.I),
    "labour": re.compile(r"\blabou?r\s*£\s*([\d,]+(?:\.\d+)?)", re.I),
    "delivery": re.compile(r"\bdelivery\s*£\s*([\d,]+(?:\.\d+)?)", re.I),
    "consumables": re.compile(r"\bconsumables\s*£\s*([\d,]+(?:\.\d+)?)", re.I),
}


def _num(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return money(Decimal(match.group(1).replace(",", "")))


def _labour_unit(text: str) -> str:
    """Only a unit attached to the labour price counts. Later '/job' delivery must not leak."""
    match = re.search(r"labou?r\s*£\s*[\d,]+(?:\.\d+)?\s*(per\s+door|per\s+job|/door|/job)?", text, re.I)
    unit = (match.group(1) or "").lower() if match else ""
    if "door" in unit and "job" not in unit:
        return "PER_DOOR"
    if "job" in unit and "door" not in unit:
        return "PER_JOB"
    return "AMBIGUOUS"


def _line(label: str, qty: int, unit: str) -> dict:
    total = eval_arithmetic(f"{qty}*{unit}")
    return {"label": label, "qty": qty, "unit": unit, "total": total, "expression": f"{qty}*{unit}"}


def quote_internal_doors(objective: str) -> dict | None:
    """Return provisional scenarios, or None when this is not a door quote."""
    if not re.search(r"internal door|labour\s*£|hinges?\s*£", objective, re.I):
        return None
    prices = {name: _num(pat, objective) for name, pat in _PRICE.items()}
    if not prices["door"] or not prices["labour"]:
        return None
    sizes = [{"width": int(m.group(1)), "height": int(m.group(2))} for m in _SIZE.finditer(objective)]
    count_match = re.search(r"(\d+)\s+internal doors", objective, re.I)
    count = len(sizes) or (int(count_match.group(1)) if count_match else 0)
    if count <= 0:
        return None
    for key in ("handle", "hinges", "consumables", "delivery"):
        prices[key] = prices[key] or "0.00"
    labour_unit = _labour_unit(objective)
    n = count
    components = _line("doors", n, prices["door"])
    handles = _line("handles", n, prices["handle"])
    hinges = _line("hinges_assumed_per_door", n, prices["hinges"])
    consumables = _line("consumables", n, prices["consumables"])
    delivery = _line("delivery", 1, prices["delivery"])
    labour_each = _line("labour_per_door", n, prices["labour"])
    labour_once = _line("labour_per_job", 1, prices["labour"])
    per_door_ex_delivery = eval_arithmetic(
        "+".join([prices["door"], prices["handle"], prices["hinges"], prices["consumables"], prices["labour"]])
    )
    total_per_door_labour = eval_arithmetic(
        "+".join([components["total"], handles["total"], hinges["total"], consumables["total"], labour_each["total"], delivery["total"]])
    )
    total_per_job_labour = eval_arithmetic(
        "+".join([components["total"], handles["total"], hinges["total"], consumables["total"], labour_once["total"], delivery["total"]])
    )
    # Second implementation: integer pence. Disagreeing with the expression parser is a defect.
    p = {k: to_pence(v) for k, v in prices.items()}
    pence_per_door = sum_pence([p["door"], p["handle"], p["hinges"], p["consumables"], p["labour"]]) * n + p["delivery"]
    pence_per_job = sum_pence([p["door"], p["handle"], p["hinges"], p["consumables"]]) * n + sum_pence([p["labour"], p["delivery"]])
    if pence_per_door != to_pence(total_per_door_labour) or pence_per_job != to_pence(total_per_job_labour):
        raise RuntimeError("door arithmetic implementations disagree")
    counts: dict[str, int] = {}
    for size in sizes:
        key = f"{size['width']}x{size['height']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "door_count": n,
        "sizes": sizes,
        "size_counts": counts,
        "prices": prices,
        "labour_unit": labour_unit,
        "hinges_unit": "ASSUMED_PER_DOOR_NOT_PROVEN",
        "vat": "UNKNOWN_NOT_APPLIED",
        "is_final_quote": False,
        "claimed_final_answered": "NO",
        "per_door_ex_delivery": per_door_ex_delivery,
        "scenarios": {
            "labour_per_door": {
                "lines": [components, handles, hinges, consumables, labour_each, delivery],
                "total_ex_vat": total_per_door_labour,
                "per_door_ex_delivery": per_door_ex_delivery,
                "is_final_quote": False,
            },
            "labour_per_job": {
                "lines": [components, handles, hinges, consumables, labour_once, delivery],
                "total_ex_vat": total_per_job_labour,
                "is_final_quote": False,
            },
        },
        "pence_check": {"labour_per_door": pence_per_door, "labour_per_job": pence_per_job},
        "missing_fields": list(MISSING_FIELDS),
        "location": "Livingston" if "livingston" in objective.lower() else "",
    }
