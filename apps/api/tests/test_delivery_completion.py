from app.intelligence.completion import score_task
from app.intelligence.quoting import quote_internal_doors
from app.intelligence.render import render_focus


def quote_report(objective):
    quote = quote_internal_doors(objective)
    facts = {"quote": quote, "calculation": None}
    return quote, render_focus("scenarios", {"task_class": "internal_door_quote", **facts}) + "\nHanding must be confirmed. Nothing was sent."


def test_quote_check_uses_this_jobs_prices():
    quote, report = quote_report("3 internal doors door £50 labour £80 handles £10 hinges £5 delivery £20")
    assert "455.00" in report and "295.00" in report
    assert score_task("internal_door_quote", report, quote=quote)["outcome"] == "PASS"
    wrong = report.replace("455.00", "1533.00")
    assert score_task("internal_door_quote", wrong, quote=quote)["outcome"] == "FAIL"


def test_explicit_units_and_vat_are_not_reported_as_ambiguous():
    quote, report = quote_report("2 internal doors door £40 labour £70 per job including VAT")
    assert "neither is selected" not in report
    assert "Use the stated labour unit" in report
    assert score_task("internal_door_quote", report, quote=quote)["outcome"] == "PASS"
    assert score_task("internal_door_quote", report.replace("INCLUDED_AS_STATED", "UNKNOWN_NOT_APPLIED"), quote=quote)["outcome"] == "FAIL"


def test_missing_quote_cannot_pass_on_old_example_prices():
    report = "1533.00 963.00 214.00 ambiguous not a final quote not applied handing. Nothing was sent."
    assert score_task("internal_door_quote", report, quote=None)["outcome"] == "FAIL"


def test_generic_research_cannot_pass_with_safety_boilerplate():
    assert score_task("business_research", "Nothing was sent. No page was opened.", {"evidence": [], "skipped": True})["outcome"] == "FAIL"
    evidence = [{"source_url": "https://supplier.test", "extracted_content": "Supplier offers doors"}]
    result = score_task("business_research", "Supplier offers doors. Nothing was sent.", {"evidence": evidence})
    assert result["outcome"] == "PARTIAL"
