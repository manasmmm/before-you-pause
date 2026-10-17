"""Tests for the deterministic engine and the caption validator.

Run from this folder with:  python -m pytest -q   (or simply: python test_appx.py)
"""
from datetime import date, timedelta

import engine as E
import validator as V


# --- engine vs the numbers shown in the deck --------------------------------

def test_cost_of_skipping_matches_deck():
    assert round(E.skipped_fv(5000, 3, .10, 51)) == 22137
    assert round(E.skipped_fv(5000, 3, .08, 51)) == 20539
    assert round(E.skipped_fv(5000, 3, .12, 51)) == 23827
    assert round(E.skipped_fv(2500, 3, .10, 51)) == 11069


def test_goal_projection_matches_deck():
    assert round(E.project_corpus(581400, 6000, 5000, .08, 51)) == 1467853
    assert round(E.project_corpus(581400, 6000, 5000, .10, 51, 3, 0)) == 1538525
    assert E.months_to_goal(581400, 6000, 5000, .10, 1500000) == 49
    assert E.months_to_goal(581400, 6000, 5000, .10, 1500000, 3, 0) == 50


def test_confidence_score_is_81_hold():
    div = E.diversification([310000, 271400, 180000], 1)
    assert round(div, 1) == 65.5
    goal = min(100, E.project_corpus(581400, 6000, 5000, .08, 51) / 1500000 * 100)
    sc = E.confidence_score(round(goal), 85, round(div), 62)
    assert sc["score"] == 81 and sc["band"] == "hold"
    assert E.confidence_score(98, 85, 66, None)["limited_history"]


def test_drawdown_context_and_thin_history():
    assert E.drawdown_context(6.8, [3] * 24) == 0.0
    assert E.drawdown_context(6.8, [3] * 5) is None


def test_recovery_episodes_exclude_open_dip():
    d0, navs, v = date(2020, 1, 1), [], 100.0
    plan = [("up", 30), ("dn", 20), ("up", 40), ("dn", 25), ("up", 50), ("dn", 15), ("up", 45), ("dn", 14)]
    for kind, days in plan:
        for _ in range(days):
            v *= 1.004 if kind == "up" else 0.9945
            navs.append((d0 + timedelta(len(navs)), v))
    eps = E.recovery_episodes(navs)
    assert eps[-1].recovered is None                       # current dip still open
    assert E.recovery_stats(eps, True) is not None
    assert E.recovery_stats(eps[:2], False) is None        # thin history is suppressed


# --- validator ---------------------------------------------------------------

PAYLOAD = {"dip_amount_inr": 19800, "dip_pct": 6.8, "corpus_inr": 271400, "gain_inr": 46400,
           "invested_inr": 225000, "instalments": 45,
           "recovery": {"n": 5, "median_days": 118, "min_days": 41, "max_days": 402}, "since": "Jan 2023"}


def test_valid_caption_renders_indian_rupees():
    good = ("Your fund is down {{dip_amount_inr}} ({{dip_pct}}) this month, to {{corpus_inr}}. "
            "Since Jan 2023 you have put in {{invested_inr}} over {{instalments}} instalments, "
            "so you are still {{gain_inr}} above it. In {{recovery.n}} earlier dips the median was "
            "{{recovery.median_days}} days.")
    r = V.validate(good, PAYLOAD)
    assert r["ok"] and "₹19,800" in r["text"] and "₹2,71,400" in r["text"]


def test_validator_fails_closed():
    assert not V.validate("You should keep going. Your fund fell ₹19,800.", PAYLOAD)["ok"]   # advice
    assert not V.validate("Your fund fell ₹25,000 this month.", PAYLOAD)["ok"]               # invented number
    assert not V.validate("Recovery is guaranteed within 90 days.", PAYLOAD)["ok"]           # banned claim
    assert not V.validate("Down {{secret}}", PAYLOAD)["ok"]                                  # unknown slot
    assert V.validate("Value is ₹2.714L today.", PAYLOAD)["ok"]                             # 2.714 L ≈ 2,71,400


SLOTS = {"dip_amount_inr": 19800, "dip_pct": 6.8, "corpus_inr": 271400, "invested_inr": 225000,
         "instalments": 45, "gain_inr": 46400, "since": "Jan 2023",
         "recovery": {"n": 5, "threshold_pct": 6, "median_days": 118, "min_days": 41, "max_days": 402},
         "change_months": 3, "skipped_inr": 15000, "assumed_return_pct": 10, "skipped_grown_inr": 22137,
         "goal_month_label": "Dec 2030", "ccs_score": 81, "ccs_max": 100, "ccs_goal": 98,
         "ccs_horizon": 85, "ccs_div": 66, "ccs_dd": 62}


def test_full_caption_examples_pass():
    out = {
        "reframe": ("Your Mid-Cap Growth Fund is down {{dip_amount_inr}} ({{dip_pct}}) this month, to "
                    "{{corpus_inr}}. Since {{since}} you have put in {{invested_inr}} over {{instalments}} "
                    "instalments, so you are still {{gain_inr}} above what you invested. In this fund's history, "
                    "{{recovery.n}} earlier dips of {{recovery.threshold_pct}} or more took a median of "
                    "{{recovery.median_days}} days to reach the prior high (range {{recovery.min_days}} to "
                    "{{recovery.max_days}} days)."),
        "cost": ("Pausing {{change_months}} instalments skips {{skipped_inr}}. At {{assumed_return_pct}} a year, "
                 "that money would have grown to about {{skipped_grown_inr}} by {{goal_month_label}}."),
        "score": ("Your score is {{ccs_score}} out of {{ccs_max}}: {{ccs_goal}} for goal coverage, "
                  "{{ccs_horizon}} for horizon, {{ccs_div}} for diversification and {{ccs_dd}} for drawdown context."),
    }
    for text in out.values():
        assert V.validate(text, SLOTS)["ok"]


def test_numeric_grounding():
    assert not V.validate("Out of 250 points you scored {{ccs_score}}", SLOTS)["ok"]   # 250 is not in the payload
    assert V.validate("Out of 100 points you scored {{ccs_score}}", SLOTS)["ok"]       # 100 is (ccs_max)


def test_hindi_lint():
    # "aapko SIP jaari rakhni chahiye" = "you should keep your SIP going" -> blocked
    assert not V.validate("आपको SIP जारी रखनी "
                          "चाहिए", SLOTS, "hi")["ok"]
    # "is mahine aapka fund {{dip}} gira hai" = "your fund fell {{dip}} this month" -> allowed
    assert V.validate("इस महीने आपका फंड "
                      "{{dip_amount_inr}} गिरा है।", SLOTS, "hi")["ok"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("ALL TESTS PASSED")
