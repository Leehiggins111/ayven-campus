"""The three requests the eval runs through the Ayven pipeline.

The third request is unseen. It is not used in prompts, gates, or tests that tune generation.
"""

ALBA_OPENER = "Create a business launch plan."

ALBA_ANSWER = (
    "The service replaces kitchen doors, worktops and handles, and it avoids a full kitchen replacement. "
    "Painting and wrapping are other services, not this service. "
    "The owner is willing to travel. Do not invent a service area, a home base, prices, testimonials, completed jobs, or guarantees. "
    "Research UK businesses that replace kitchen doors, worktops and handles. "
    "Cover the service, the customer, the problem, the offer, competitor research from real websites, "
    "pricing assumptions, channels, ad copy, a call to action, next steps, evidence, assumptions, and unresolved items."
)

COFFEE = (
    "Prepare a UK business briefing for a coffee machine rental company. "
    "Cover the service, the customers, the competitors, and pricing."
)

# Unseen. Do not copy this trade into engine prompts or into a test that changes the gate.
UNSEEN = (
    "Create a business launch plan for a mobile dog-grooming service in the UK. "
    "The service grooms dogs at the customer's home or in a van, and it is not a kennel or a veterinary practice. "
    "The owner is willing to travel. Do not invent a service area, a home base, prices, testimonials, completed jobs, or guarantees. "
    "Research UK businesses that groom dogs at home or from a mobile van. "
    "Cover the service, the customer, the problem, the offer, competitor research from real websites, "
    "pricing assumptions, channels, ad copy, a call to action, next steps, evidence, assumptions, and unresolved items."
)

REQUESTS = (
    {"id": "alba", "title": "alba", "objective": ALBA_OPENER, "answer": ALBA_ANSWER},
    {"id": "coffee", "title": "coffee", "objective": COFFEE, "answer": ""},
    {"id": "unseen", "title": "unseen", "objective": UNSEEN, "answer": ""},
)

# Library listings checked on 2026-10-11. The runner replaces file_gb with the pulled size.
CANDIDATES = (
    {
        "tag": "qwen3:4b",
        "listed_gb": 2.5,
        "licence": "Apache-2.0",
        "restrictions": "None beyond the Apache-2.0 terms. Qwen states the open-weight models are Apache 2.0.",
        "source": "https://github.com/QwenLM/Qwen3",
        "ran": True,
    },
    {
        "tag": "qwen3:8b",
        "listed_gb": 5.2,
        "licence": "Apache-2.0",
        "restrictions": "None beyond the Apache-2.0 terms.",
        "source": "https://github.com/QwenLM/Qwen3",
        "ran": True,
    },
    {
        "tag": "qwen3:14b",
        "listed_gb": 9.3,
        "licence": "Apache-2.0",
        "restrictions": "None beyond the Apache-2.0 terms. The Q4 file is about 9.3 GB, which is the borderline of the 9-10 GB install cap.",
        "source": "https://ollama.com/library/qwen3:14b",
        "ran": True,
    },
    {
        "tag": "gemma3:12b",
        "listed_gb": 8.2,
        "licence": "Gemma Terms of Use",
        "restrictions": (
            "Not an Apache or MIT licence. Use must follow the Gemma Prohibited Use Policy, "
            "and anyone who redistributes the weights must pass those restrictions on. "
            "Google may restrict use it believes breaks the terms."
        ),
        "source": "https://ai.google.dev/gemma/terms",
        "ran": True,
    },
    {
        "tag": "phi4",
        "listed_gb": 9.1,
        "licence": "MIT",
        "restrictions": (
            "The licence is MIT. The model card asks users to check accuracy, safety, and applicable law, "
            "and notes the model is focused on English. Those notes are not extra licence terms."
        ),
        "source": "https://huggingface.co/microsoft/phi-4",
        "ran": True,
    },
    {
        "tag": "mistral-small:24b",
        "listed_gb": 14,
        "licence": "Apache-2.0",
        "restrictions": (
            "Apache-2.0. The Q4 file is about 14 GB, which does not fit a free runner disk "
            "or the 9-10 GB install cap on a 16 GB machine. It is not run."
        ),
        "source": "https://ollama.com/library/mistral-small:24b",
        "ran": False,
    },
)
