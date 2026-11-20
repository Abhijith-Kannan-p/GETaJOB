"""Grounding check: pure unit + property tests (no DB, no LLM)."""

from hypothesis import given
from hypothesis import strategies as st

from app.agent.grounding import check_claim, ground, numbers
from app.ingest.skills import load_taxonomy

TAX = load_taxonomy()


def find(text):
    return [m.skill for m in TAX.find(text)]


FACTS = {
    "F1": "Your profile: skills Python, SQL; target roles data analyst intern.",
    "F2": 'Job 17 "Data Analyst Intern" at Acme ranks #4 of 200 for your profile (score 0.61).',
    "F3": "Adding Tableau: 12 jobs in your pool list Tableau and move up 3.5 places on average; effort about 20 hours.",
}


def test_numbers_are_canonicalised():
    assert numbers("rank #4 of 1,000, score 0.60, 3.50 h, 007") == {"4", "1000", "0.6", "3.5", "7"}


def test_grounded_claim_is_kept():
    assert check_claim("Data Analyst Intern at Acme is #4 of 200.", ["F2"], FACTS, find) is None
    assert check_claim("Tableau moves 12 jobs up by 3.5 places.", ["F3"], FACTS, find) is None


def test_each_failure_mode_is_caught():
    assert check_claim("You are #4.", [], FACTS, find) == "cites no fact"
    assert check_claim("You are #4.", ["F9"], FACTS, find) == "cites unknown fact F9"
    assert check_claim("You are #3 of 200.", ["F2"], FACTS, find) == "number 3 not in cited facts"
    assert check_claim("Learn Kubernetes to rank #4.", ["F2"], FACTS, find) == "skill Kubernetes not in cited facts"
    assert check_claim("See https://example.com", ["F2"], FACTS, find) == "contains a link"
    assert check_claim("x" * 301, ["F2"], FACTS, find) == "too long"
    # a number that exists only in a DIFFERENT, uncited fact does not count
    assert check_claim("Tableau is listed by 200 jobs.", ["F3"], FACTS, find) == "number 200 not in cited facts"


def test_ground_drops_bad_parts_and_promotes_a_point():
    raw = {
        "headline": "You are #1 everywhere.",  # invented number
        "headline_facts": ["F2"],
        "points": [
            {"text": "Data Analyst Intern at Acme is #4 of 200.", "facts": ["F2"]},
            {"text": "Docker would help.", "facts": ["F3"]},  # skill not in the cited fact
        ],
        "next_steps": [
            {"skill": "tableau", "why": "It moves 12 of your jobs up.", "facts": ["F3"]},
            {"skill": "Underwater Basket Weaving", "why": "Fun.", "facts": ["F3"]},
            {"skill": "Docker", "why": "Popular.", "facts": ["F3"]},
        ],
    }
    r = ground(raw, FACTS, find, TAX.canonical)
    assert r.narrative.headline.text == "Data Analyst Intern at Acme is #4 of 200."
    assert r.narrative.points == []
    assert [s.skill for s in r.narrative.next_steps] == ["Tableau"]
    reasons = sorted(d["reason"] for d in r.dropped)
    assert reasons == [
        "not a known skill",
        "number 1 not in cited facts",
        "skill Docker not in cited facts",
        "skill not in cited facts",
    ]


def test_ground_survives_garbage_shapes():
    r = ground({"headline": 5, "points": "nope", "next_steps": [None, 3]}, FACTS, find, TAX.canonical)
    assert r.narrative.headline is None and r.kept == 0


# ---------------- properties ----------------

ints = st.integers(min_value=0, max_value=100_000)


@given(job=ints, rank=ints, pool=ints, pick=st.lists(st.sampled_from(["job", "rank", "pool"]), max_size=3))
def test_claims_using_only_cited_numbers_are_always_kept(job, rank, pool, pick):
    facts = {"F1": f"Job {job} ranks #{rank} of {pool} for your profile."}
    vals = {"job": job, "rank": rank, "pool": pool}
    claim = "You are " + " and ".join(f"#{vals[p]}" for p in pick) + " here." if pick else "Nice match."
    assert check_claim(claim, ["F1"], facts, find) is None


@given(job=ints, rank=ints, extra=ints)
def test_any_number_absent_from_cited_facts_is_rejected(job, rank, extra):
    facts = {"F1": f"Job {job} ranks #{rank}."}
    if str(extra) in {str(job), str(rank)}:
        return
    reason = check_claim(f"Job {job} ranks #{extra}.", ["F1"], facts, find)
    assert reason == f"number {extra} not in cited facts"
