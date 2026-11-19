"""Player cards via Parallel Task API + JSON schema.

Team, number, position, 3 fun facts, per-season stats table, each with `basis`.
Output: data/players/<slug>.json
"""


def collect_player(name: str, team: str) -> dict:
    raise NotImplementedError
