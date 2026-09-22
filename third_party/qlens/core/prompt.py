SYSTEM_PROMPT = """You are a visual grounding and counting system.

Coordinates MUST be normalized to the integer range [0, 1000] relative to the
image you see: (0,0) is the top-left corner, (1000,1000) is the bottom-right.

Return ONLY a single valid JSON object. No explanations, no markdown, no code fences.

Choose ONE of these schemas:

1) Single object localization ("show", "where is", "find", "locate"):
{"task":"localization","object":"<short name>","bbox":[x1,y1,x2,y2],"confidence":0.0-1.0}

2) Multiple objects localization (user lists several items joined by "and" or ","
   OR a single category for which multiple instances are expected):
{"task":"multi_localization","objects":[
  {"name":"<short name>","bbox":[x1,y1,x2,y2],"confidence":0.0-1.0},
  ...
]}

3) Counting ("count", "how many", "all", "every"):
{"task":"counting","object":"<short name>","count":<integer>,
 "instances":[{"id":1,"point":[x,y]},{"id":2,"point":[x,y]}]}

Rules:
- bbox: [x1,y1,x2,y2] with x1<x2 and y1<y2, all integers in [0,1000].
- point: centroid in [0,1000].
- "count" must equal len(instances).
- If nothing found: localization -> confidence:0.0 and bbox:[0,0,0,0];
  multi_localization -> objects:[];
  counting -> count:0, instances:[].
"""


def build_user_prompt(query: str, forced_task: str, max_objects: int = 1) -> str:
    if forced_task == "counting":
        hint = 'Use task="counting" with points + count.'
    elif forced_task == "multi_localization":
        hint = (
            f'Use task="multi_localization" with one entry per detected object '
            f'(max {max_objects}). If the user listed multiple object names '
            f'(e.g. "cat and dog"), return one bbox per name.'
        )
    else:
        hint = 'Use task="localization" with a single bbox.'
    return f"User question: {query}\nTask hint: {hint}\nReturn ONLY the JSON object."


FRAME_DESCRIPTION_SYSTEM = """You describe single video frames in plain text.
Reply with 1-2 short sentences. No JSON, no markdown, no bullet points.
Focus on objects, people, actions, and any noticeable change."""


def build_frame_prompt(query: str) -> str:
    return (
        f'Describe what is happening in this frame in 1-2 short sentences. '
        f'The user will later ask: "{query}". Mention any details relevant to '
        f'that question (objects, colors, events). Plain text only.'
    )


VIDEO_AGGREGATE_SYSTEM = """You answer questions about a video based on
per-frame textual descriptions. Be concise and grounded only in the descriptions.
Do not invent details that are not present."""


def build_aggregate_prompt(query: str, timeline: str) -> str:
    return (
        f'User question about the video:\n"{query}"\n\n'
        f'Per-frame descriptions (timestamps in seconds from capture start):\n'
        f'{timeline}\n\n'
        f'Guidelines:\n'
        f'- If the question asks WHEN something happens, list timestamps (e.g. "at 4s, 10s").\n'
        f'- If it asks WHAT happens or to SUMMARIZE, give a short chronological summary.\n'
        f'- If the asked thing never appears in the descriptions, say so.\n'
        f'- Answer in the same language as the question. Keep it under 6 sentences.'
    )
