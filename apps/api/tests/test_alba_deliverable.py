"""The Alba launch plan must not be marked complete on off-topic pages or an empty template."""

from app.intelligence.audit import authoritative_decision
from app.intelligence.completion import score_task
from app.intelligence.deliverable import (
    business_can_complete,
    finished_lines,
    page_is_relevant,
    plan_sections_filled,
    publish_business_plan,
    reject_search_query,
)
from app.intelligence.execution import _approved_research_is_unresolved
from app.intelligence.render import render_focus
from app.intelligence.research import plan_queries, research
from app.intelligence.resolution import resolve_manager
from app.tools import extract_main_text

OBJECTIVE = (
    "Create a business launch plan.\n"
    "Lee answered: Alba Kitchen Refresh, a local kitchen painting service in the UK. "
    "Cover the service, the customer, the problem, the offer, competitor research from real websites, "
    "pricing assumptions, channels, ad copy, a call to action, next steps, evidence, assumptions, and unresolved items."
)

# The pages opened for that objective on the Windows run Lee rejected.
JUNK = (
    ("https://wordpress.com/start", "WordPress.com", "WordPress.com Start a website builder and blog."),
    ("https://workspace.google.com/products/sites/", "Google Sites: Website Creator and Hosting | Google Workspace", "Google Sites website creator for business. Gmail Drive Meet Docs Sheets."),
    ("https://www.wix.com/", "Website Builder - Create a Free Website In Minutes | Wix.com", "Create your future on the leading website builder. 300M+ Sites built on Wix."),
    ("https://support.google.com/a/users/answer/9310491?hl=en", "Create your first site with Google Sites", "Skip to main content. Create your first site with Google Sites. Add and organize pages."),
    ("https://wordpress.com/website-builder/", "WordPress Website Builder | Start for Free", "Build Website Ecommerce AI website builder Publish Blog Hosting."),
    ("https://leeprecision.com/", "Lee Precision, Inc.", "Online Dealers. Shopping cart (0). Select your Cartridge. 9mm 45 ACP 223 ammunition reloading."),
    ("https://www.lee.com/", "Lee Jeans", "Lee jeans shop men women denim originals."),
    ("https://www.albakitchen.com/", "High End Kitchen Cabinets NJ", "Renovate Your Kitchen Cabinets In NJ. Talk to our kitchen designers for high-end kitchen cabinets."),
    ("https://www.albakitchen.com/shop", "Shop All", "Fabuwood Tuscany kitchen cabinet ranges and showroom stock."),
    ("https://kitchenrefresh.net/", "Kitchen Remodeling That's Fast & Affordable | Kitchen Refresh", "Kitchen remodeling franchise. Finish styles, warranty, own a franchise."),
    ("https://alba-professional.com/", "ALBA Professional | When a Professional Cooks - Since 1883", "Commercial kitchen solutions and modular equipment for professional cooks."),
    ("https://www.albakitchens.com/kitchen-brochure", "Kitchen Brochure | alba", "Kitchen brochure pdf. Click on the thumbnail to view the brochure."),
    ("https://kitchenrefresh.net/contact-us/", "Contact Kitchen Refresh | Get a Free Consultation", "Free consultation for kitchen remodeling. Estimator locations inspiration."),
    ("https://lp.albakitchen.com/", "lp.albakitchen.com", "NJ trusted name in kitchen cabinets. Custom-made kitchen cabinets. Free quote."),
    ("https://www.albasinks.com/products", "Alba Sinks - Products", "Undermount kitchen sinks, apron fronts, vanity sinks and faucets."),
    ("https://cover-corp.com/en/company", "Company | COVER Corp.", "VTuber production, media mix and the metaverse. COVER Corp company mission."),
    ("https://cover-corp.com/en", "Top | COVER Corp.", "From VTubers to the metaverse. Hololive and virtual creators."),
    ("https://undercovertonneaucover.com/", "undercovertonneaucover.com", "Tonneau covers for pickup trucks."),
    ("https://www.covercity.net/", "CoverCity - DVD Covers & Labels", "Custom DVD covers and labels. Recent uploads."),
    ("https://www.covergirl.com/", "COVERGIRL", "Makeup and beauty products. Skip to main content. Foundation and lipstick."),
    ("https://www.covers.com/", "Covers.com - Sports Betting Odds, Lines, Picks & News 2026", "Sports betting odds, lines, picks and sportsbook bonus codes."),
)

PAINTER = {
    "url": "https://example-painter.example/kitchen-painting-prices",
    "title": "Kitchen cabinet painting prices in Manchester",
    "text": "We paint and respray kitchen cabinets for homeowners across the UK. A site visit comes before any quote.",
}


def _preview(url: str, title: str, text: str) -> dict:
    return {"url": url, "source_url": url, "title": title, "source_title": title, "text": text, "extracted_content": text}


def _filled_plan() -> str:
    return "\n".join([
        "Business launch plan",
        "Service",
        "Alba Kitchen Refresh paints and resprays the kitchen cabinets people already have, for homeowners who want a new look without a full replacement.",
        "Target customer",
        "Local UK homeowners who like their kitchen layout and want the cabinets painted rather than ripped out.",
        "Problem",
        "A full kitchen replacement costs more and takes longer than most households want when the units themselves are still sound.",
        "Offer and positioning",
        "The offer is an on-site cabinet painting service positioned against replacement showrooms: shorter disruption, the existing kitchen stays, and the finish is the thing being sold.",
        "Competitor and market research",
        "Opened sources describe cabinet painting and respray work. No opened page was a UK painting competitor in this fixture, so the positioning stays cautious.",
        "Pricing",
        "ASSUMPTION: a price is not stated on the opened page, so the plan does not publish one. The reason is that a durable quote depends on the cabinet count and the condition seen at a visit.",
        "Channels",
        "Start with local search and a short homeowner leaflet near the service area. Do not buy ads until a page states a result worth copying.",
        "Advert",
        "Headline: New colour, same kitchen.",
        "Body: Alba Kitchen Refresh paints the cabinets you already own, so the kitchen can look finished without a full refit.",
        "Call to action: Ask for a visit and a written scope before you decide.",
        "Call to action",
        "Ask for a visit and a written scope. No booking is made from this plan.",
        "Next steps",
        "Confirm the towns to cover, open two UK painting companies' pages, and only then set a price assumption beside those pages.",
        "Assumptions",
        "The business name and the UK painting service come from the request. Demand is not proven by the name alone.",
        "Unresolved",
        "The towns, the day rate, and which UK painters are the real local alternatives are still open.",
        "Nothing was sent.",
    ])


def test_queries_do_not_split_on_the_requester_or_instruction_words():
    planned, source = plan_queries("business_research", OBJECTIVE)
    blob = " ".join(planned).lower()
    assert source == "objective-fallback"
    assert "lee official" not in blob
    assert "cover official" not in blob
    assert "website" not in blob
    assert "kitchen" in blob and "painting" in blob
    assert reject_search_query("Lee official site", OBJECTIVE) == "requester_name"
    assert reject_search_query("Cover official site", OBJECTIVE) == "instruction_only"
    assert reject_search_query("your website", OBJECTIVE) == "pronoun"
    assert reject_search_query("kitchen cabinet painting UK prices", OBJECTIVE) == ""


def test_junk_pages_are_not_relevant_and_a_painting_page_is():
    for url, title, text in JUNK:
        assert page_is_relevant(OBJECTIVE, title, text, url) is False, url
    assert page_is_relevant(OBJECTIVE, PAINTER["title"], PAINTER["text"], PAINTER["url"]) is True


def test_junk_evidence_is_dropped_and_cannot_be_completed():
    def search(_query, limit=8):
        return [{"title": title, "url": url, "snippet": text[:180]} for url, title, text in JUNK][:limit]

    def fetch(url):
        title, text = next((item[1], item[2]) for item in JUNK if item[0] == url)
        return {"url": url, "title": title, "text": text, "error": ""}

    result = research(
        "business_research",
        OBJECTIVE,
        "pkg-alba-junk",
        "research-e3",
        queries=["kitchen painting UK prices"],
        max_rounds=1,
        search_fn=search,
        fetch_fn=fetch,
    )
    assert result["evidence"] == []
    assert any(item.get("error") == "off_topic" for item in result["failures"])
    previews = [_preview(url, title, text) for url, title, text in JUNK]
    skeleton = render_focus("draft", {"task_class": "business_research", "objective": OBJECTIVE, "research": {"evidence": previews, "gaps": []}})
    assert plan_sections_filled(skeleton) is False
    score = score_task("business_research", skeleton, {"objective": OBJECTIVE, "evidence": previews})
    assert score["safety_outcome"] == "PASS"
    assert score["outcome"] == "FAIL"
    assert authoritative_decision(skeleton, "business_research", "draft", attempt=1) != "ACCEPT"
    assert business_can_complete(skeleton, previews, OBJECTIVE) is False
    assert business_can_complete(_filled_plan(), previews, OBJECTIVE) is False
    row = {
        "task_class": "business_research",
        "objective": "Create a business launch plan.",
        "clarification_answer": OBJECTIVE.split("Lee answered:", 1)[-1].strip(),
        "observability_json": __import__("json").dumps({
            "research_mode": "live",
            "pages": [url for url, _title, _text in JUNK],
            "understood_objective": OBJECTIVE,
            "evidence_preview": previews,
            "contract_evaluation": {"passed": True},
        }),
    }
    assert _approved_research_is_unresolved(row, _filled_plan()) is True
    assert _approved_research_is_unresolved(row, skeleton) is True
    manager = resolve_manager(
        task_class="business_research",
        audits=[{"focus": "draft", "decision": "ACCEPT"}],
        claims=[],
        quote=None,
        research={"evidence": previews, "rounds_exhausted": True, "gaps": [], "deliverable_filled": False},
    )
    assert manager["decision"] == "ESCALATE"


def test_a_relevant_page_and_a_written_plan_can_complete():
    evidence = [_preview(PAINTER["url"], PAINTER["title"], PAINTER["text"])]
    plan = _filled_plan()
    assert plan_sections_filled(plan, PAINTER["text"]) is True
    assert business_can_complete(plan, evidence, OBJECTIVE) is True
    score = score_task("business_research", plan, {"objective": OBJECTIVE, "evidence": evidence})
    assert score["outcome"] == "PASS"
    row = {
        "task_class": "business_research",
        "objective": OBJECTIVE,
        "observability_json": __import__("json").dumps({
            "research_mode": "live",
            "pages": [PAINTER["url"]],
            "understood_objective": OBJECTIVE,
            "evidence_preview": evidence,
            "contract_evaluation": {"passed": True},
        }),
    }
    assert _approved_research_is_unresolved(row, plan) is False


def test_extraction_keeps_the_article_and_drops_navigation():
    html = """
    <html><head><title>Kitchen painting</title></head><body>
    <nav>Skip to main content Shopping cart (0) top of page Log in</nav>
    <article><h1>Kitchen cabinet painting</h1>
    <p>A Manchester firm paints kitchen cabinets and quotes after a visit.</p></article>
    <footer>Privacy policy</footer>
    </body></html>
    """
    text = extract_main_text(html)
    assert "paints kitchen cabinets" in text
    assert "Shopping cart" not in text
    assert "Skip to main content" not in text


def test_planner_instructions_are_not_searched(monkeypatch):
    from app.models import set_role_generator

    echo = "\n".join([
        "The queries must:",
        "Name the service, the place, or a price.",
        "Shape: e.g., 'kitchen cabinet painting UK prices' or 'kitchen respray company prices'",
        "Do not search the requester's name.",
        "Do not search pronouns.",
        "One query per line, each starting with '- '",
        "s name (so avoid",
        "The objective is to create a business launch plan for a kitchen painting service in the UK and it should not be searched as a sentence.",
    ])
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")

    def gen(role, system, user, max_tokens):
        return echo, 12, {"backend": "generator"}

    set_role_generator(gen)
    try:
        planned, source = plan_queries("business_research", OBJECTIVE)
    finally:
        set_role_generator(None)
    blob = " ".join(planned).lower()
    assert source == "model"
    assert "pronoun" not in blob
    assert "do not" not in blob
    assert "the queries must" not in blob
    assert "kitchen cabinet painting uk prices" in blob
    assert "kitchen respray company prices" in blob
    assert "s name" not in blob


def test_a_trade_page_is_opened_when_the_snippet_score_is_low(monkeypatch):
    monkeypatch.setattr(
        "app.intelligence.research.plan_queries",
        lambda *args, **kwargs: (["kitchen cabinet painting UK prices"], "model"),
    )

    def search(_query, limit=8):
        return [
            {
                "title": "Kitchen cabinet painting cost",
                "url": "https://albadecor.co.uk/blog/kitchen-cabinet-painting-vs-replacement-cost-guide-2025",
                "snippet": "Cost guide",
            },
            {
                "title": "Baby names",
                "url": "https://www.momjunction.com/articles/ugly-and-dumb-baby-names",
                "snippet": "names to avoid",
            },
        ]

    def fetch(url):
        if "albadecor" in url:
            return {
                "url": url,
                "title": "Kitchen cabinet painting",
                "text": "We paint and respray kitchen cabinets in the UK. The price is agreed after a visit.",
                "error": "",
            }
        return {"url": url, "title": "Baby names", "text": "Popular baby names to avoid this year.", "error": ""}

    result = research(
        "business_research",
        OBJECTIVE,
        "pkg-trade-score",
        "research-e3",
        max_rounds=1,
        search_fn=search,
        fetch_fn=fetch,
    )
    urls = [item.get("source_url") or "" for item in result["evidence"]]
    assert any("albadecor.co.uk" in url for url in urls)
    assert not any("momjunction" in url for url in urls)
    assert not any("albadecor" in (item.get("url") or "") and item.get("reason") == "below_relevance_threshold" for item in result.get("filtered") or [])


def test_bold_and_inline_headings_still_count_as_a_written_plan():
    text = "\n".join([
        "**Service**",
        "Alba Kitchen Refresh paints existing kitchen cabinets in the customer's home.",
        "**Target customer:** UK homeowners who want the cabinets painted rather than replaced.",
        "Problem: A full replacement is more disruption than the household wants when the units are sound.",
        "Offer and positioning: The offer is an on-site cabinet respray that leaves the kitchen in place and sells the finish.",
        "Competitor and market research",
        "Opened painting pages describe respray work. This plan does not invent a competitor the pages did not name.",
        "Pricing",
        "ASSUMPTION: a price is not stated on the opened page, so the plan does not publish one. A visit has to count the doors.",
        "Channels",
        "Use local search and a short conversation with homeowners. Nothing was sent.",
        "Advert",
        "Headline: Same kitchen, new colour.",
        "Body: Alba paints the cabinets you already own so the room can change without a refit.",
        "Call to action: Ask what the visit includes before you book.",
        "Call to action",
        "Ask for a visit and a written scope. No booking was made.",
        "Next steps",
        "Open two UK kitchen painting pages and confirm the towns before any price is shown.",
        "Assumptions",
        "The service and the UK location come from the request. Demand is not proven.",
        "Unresolved",
        "The towns and the day rate are still open.",
        "Nothing was sent.",
    ])
    assert plan_sections_filled(text) is True


def _worksheet() -> str:
    """The draft qwen3:4b stored on the Windows run: a scratchpad, not a launch plan."""
    return "\n".join([
        "Business launch plan",
        "Service",
        "The service is kitchen painting for UK homeowners who want the cabinets refreshed without a full renovation.",
        "Target customer",
        "UK homeowners with dated kitchen cabinets who want a refresh without full renovation.",
        "Problem",
        "The evidence doesn't explicitly state what the customer's problem is.",
        "This is unresolved.",
        "Unresolved - the evidence doesn't specify what problem Alba Kitchen Refresh solves.",
        "Unresolved.",
        "Offer and positioning",
        "The evidence doesn't state what Alba Kitchen Refresh specifically offers or how it differs from other providers.",
        "This is unresolved.",
        "Unresolved.",
        "Competitor and market research",
        "I have evidence from opened pages about kitchen cabinet painting prices in the UK.",
        "Airtasker lists professional cabinet painting at £80 to £100.",
        "Pricing",
        "The Airtasker page shows £80 to £100.",
        "I'll need to make an assumption based on the evidence.",
        "Channels",
        "The evidence doesn't specify channels.",
        "This is unresolved.",
        "Unresolved.",
        "The evidence doesn't specify channels that Alba Kitchen Refresh would use.",
        "Advert",
        "I need to create a 3-line ad copy.",
        "Headline: Refresh your kitchen without the cost of a full renovation.",
        "Body: Cabinet painting updates the kitchen the household already owns.",
        "Call to action",
        "This needs to be a full sentence.",
        "Contact Alba Kitchen Refresh and ask what a visit includes before any booking.",
        "Next steps",
        "1.",
        "Confirm the towns to cover, then set a price assumption beside the opened pages.",
        "Assumptions",
        "What I need to assume based on the evidence.",
        "Homeowners with dated cabinets are the people the request describes.",
        "Unresolved",
        "Let me draft each section.",
        "I think the draft meets the guidelines.",
        "Nothing was sent.",
    ])


def test_a_reasoning_monologue_is_not_a_finished_plan():
    """qwen3 wrote its reading of the prompt into the answer. That is not the plan."""
    monologue = "\n".join([
        "Business launch plan",
        "Service",
        "Professional kitchen cabinet painting service in",
        "Target customer",
        "Okay, the user wants me to write a section of a launch plan for a UK kitchen painting service owner.",
        "Looking at the context, I see they've provided several opened pages with pricing information.",
        "Problem",
        "Hmm, looking at the context, this is for Lee who's creating a business launch plan.",
        "I should focus on the problem statement.",
        "Offer and positioning",
        "Okay, the user wants me to write the offer. The prices vary widely.",
        "Pricing",
        "Hmm, the user wants the pricing section to begin with Assumption.",
        "Channels",
        "Okay, the user wants me to write how the service reaches homeowners.",
        "Advert",
        "Hmm, the user wants exactly three lines. They want something",
        "Call to action",
        "Okay, the user wants a call to action. I should avoid quoting prices.",
        "Next steps",
        "Okay, the user wants the next steps, including confirming towns to cover.",
        "Nothing was sent.",
    ])
    assert plan_sections_filled(monologue) is False
    assert "okay, the user" not in finished_lines(monologue).lower()
    assert plan_sections_filled(_filled_plan()) is True


def test_a_drafting_worksheet_is_not_a_finished_plan():
    worksheet = _worksheet()
    assert plan_sections_filled(worksheet) is False
    assert plan_sections_filled(_filled_plan()) is True
    cleaned = finished_lines(worksheet)
    assert "I'll need" not in cleaned
    assert "doesn't state" not in cleaned
    assert "Let me draft" not in cleaned
    assert "Refresh your kitchen" in cleaned
    assert plan_sections_filled(cleaned) is False
    published = publish_business_plan(OBJECTIVE, {"evidence": [_preview(PAINTER["url"], PAINTER["title"], PAINTER["text"])]}, worksheet)
    assert published == ""


def test_narration_around_a_real_offer_is_dropped_and_the_offer_remains():
    text = _filled_plan().replace(
        "The offer is an on-site cabinet painting service positioned against replacement showrooms: shorter disruption, the existing kitchen stays, and the finish is the thing being sold.",
        "The evidence doesn't state what Alba offers.\n"
        "Let me draft the offer.\n"
        "The offer is an on-site cabinet painting service positioned against replacement showrooms: shorter disruption, the existing kitchen stays, and the finish is the thing being sold.",
    )
    assert plan_sections_filled(text) is False
    assert plan_sections_filled(finished_lines(text)) is True


def test_the_blank_form_is_not_the_draft_the_model_continues():
    from types import SimpleNamespace

    from app.intelligence.execution import _employee_user

    programme = SimpleNamespace(
        objective=OBJECTIVE,
        task_class="business_research",
        research={"evidence": [_preview(PAINTER["url"], PAINTER["title"], "Professional kitchen cabinet painting costs £80 to £100 in the UK.")]},
    )
    child = {
        "focus": "draft",
        "report": "Unresolved. No opened page stated an offer or how it differs from other providers.",
    }
    user = _employee_user(programme, child)
    assert "how it differs from other providers" not in user
    assert "No owner plan has been written yet" in user
    assert "£80" in user
    assert PAINTER["url"] in user


def test_a_missing_plan_is_written_one_section_at_a_time(monkeypatch):
    from types import SimpleNamespace

    from app.intelligence.execution import _publish_plan

    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    programme = SimpleNamespace(
        objective=OBJECTIVE,
        parent_id="pkg-sections",
        skills=[],
        research={"evidence": [_preview(
            PAINTER["url"],
            PAINTER["title"],
            "Professional kitchen cabinet painting costs £80 to £100 in the UK. We paint and respray kitchen cabinets.",
        )]},
    )
    answers = {
        "Service": "Alba Kitchen Refresh paints and resprays the kitchen cabinets a household already owns.",
        "Target customer": "Local UK homeowners who want the cabinets painted rather than ripped out.",
        "Problem": "Replacing a kitchen costs more and takes longer when the units themselves are still sound.",
        "Offer and positioning": "The offer is an on-site cabinet painting service, which differs from a showroom replacement because the existing kitchen stays.",
        "Competitor and market research": "Opened pages describe UK cabinet painting prices, including a range of £80 to £100, and none of them is Alba.",
        "Pricing": "ASSUMPTION: plan around £80 to £100 from the opened painting page, because a visit still has to count the doors.",
        "Channels": "Start with local search and a short conversation near the service area. Nothing was sent.",
        "Advert": "Headline: New colour, same kitchen.\nBody: Alba paints the cabinets you already own so the room can change without a full refit.\nCall to action: Ask for a visit and a written scope.",
        "Call to action": "Ask for a visit and a written scope before any booking. Nothing was sent.",
        "Next steps": "Confirm the towns to cover, then set the visit before any price is published. Nothing was sent.",
        "Assumptions": "The name and the UK painting service come from the request. Demand is not proven by the name alone.",
        "Unresolved": "The towns to cover and the condition of the cabinets stay open until a visit.",
    }

    by_prefill = {
        "Alba Kitchen Refresh paints ": answers["Service"],
        "The customer is a UK homeowner who ": answers["Target customer"],
        "A full kitchen replacement ": answers["Problem"],
        "The offer is ": answers["Offer and positioning"],
        "Opened UK pages state ": answers["Competitor and market research"],
        "Assumption: ": answers["Pricing"],
        "Homeowners nearby are reached through ": answers["Channels"],
        "Headline: ": answers["Advert"],
        "Ask for a visit and a written scope ": answers["Call to action"],
        "Next, the owner ": answers["Next steps"],
        "The plan assumes ": answers["Assumptions"],
        "Still open: ": answers["Unresolved"],
    }

    def fake(role, system, user, max_tokens=320, programme=None, package_id="", plain=False, prefill=""):
        if prefill in by_prefill:
            return by_prefill[prefill], 12, {"backend": "test"}
        raise AssertionError(prefill or user[:160])

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    published = _publish_plan(programme, "This is an essay with no headings and no offer.")
    assert "paints and resprays" in published
    assert "assumption" in published.lower()
    assert "Headline:" in published
    assert "local search" in published
    assert "towns to cover" in published
    assert plan_sections_filled(published, "costs £80 to £100") is True


def test_opened_pages_stay_ahead_of_failed_fetches():
    from app.intelligence.campus_view import _evidence

    failures = [
        {
            "claim_text": "Retrieval failed at fetch: Client error '403 Forbidden'",
            "claim_type": "SOURCE_FAILURE",
            "freshness": "UNKNOWN",
            "status": "UNVERIFIED",
            "source_url": f"https://example.invalid/fail-{index}",
            "evidence_text": '{"query": "kitchen respray company prices", "stage": "fetch"}',
            "package_id": "gaps",
        }
        for index in range(12)
    ]
    live = {
        "claim_text": "Professional kitchen cabinet painting costs £80 to £100 in the UK.",
        "claim_type": "FACT",
        "freshness": "LIVE",
        "status": "SUPPORTED",
        "source_url": PAINTER["url"],
        "source_title": PAINTER["title"],
        "evidence_text": PAINTER["text"],
        "package_id": "evidence",
    }
    rows = _evidence(failures + [live], [], {}, {"research_mode": "live"})
    live_rows = [row for row in rows if row.get("provenance") == "LIVE" and "http" in (row.get("sentence") or "")]
    assert live_rows
    assert PAINTER["url"] in live_rows[0]["sentence"]
    assert rows[0]["provenance"] == "LIVE"
