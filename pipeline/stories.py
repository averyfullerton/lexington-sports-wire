"""5 Big Stories: Search + Extract -> Decisions (safety) -> rank -> write.

Gather the week's news from allowlisted sources, filter for safety (fail closed),
pick the 5 most interesting kid-friendly storylines, write each at first-grade level.
Output: data/stories.json
"""


def gather() -> list[dict]:
    raise NotImplementedError


def rank(stories: list[dict], n: int = 5) -> list[dict]:
    raise NotImplementedError
