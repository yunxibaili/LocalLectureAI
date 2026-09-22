import re

# Trigger words that route the query to the "counting" task.
COUNT_KEYWORDS = [
    r"\bcount\b", r"\bhow many\b", r"\ball of\b", r"\ball\b", r"\bevery\b",
    r"\bnumber of\b", r"\btotal\b",
]
_COUNT_PATTERN = re.compile("|".join(COUNT_KEYWORDS), re.IGNORECASE)


def detect_task(query: str, max_objects: int = 1) -> str:
    """Return 'counting', 'multi_localization', or 'localization'."""
    q = query or ""
    if _COUNT_PATTERN.search(q):
        return "counting"
    if max_objects > 1:
        return "multi_localization"
    return "localization"
