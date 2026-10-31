"""The Politics day these logic tests were written against (2026-10-21).

The live Politics moved to 15 slots from 06:30 (config/accounts.json). The tests that build their own throwaway
accounts.json test signing, posting, cuts and cards against a known clock ("07:00 is an hour away at 06:00"), not
the live schedule, so they pin the Politics's slots here, the same way they already pin delivered folders, seconds
and outlets. The live schedule itself is checked against config/accounts.json in test_new_accounts and
test_one_truth_docs."""

POLITICS_TEST_SLOTS = ["07:00", "08:45", "10:30", "12:15", "14:00", "15:45", "17:30", "19:15", "21:00", "23:00"]


def pin_politics(raw: dict) -> dict:
    """Pin the Politics in a raw accounts.json dict to the ten test slots (in place; returns raw). Gaming and
    Stunts are pinned with it (pin_gta)."""
    led = raw["accounts"]["politics"]
    led["slots"] = list(POLITICS_TEST_SLOTS)
    led["daily_target"] = len(POLITICS_TEST_SLOTS)
    return pin_gta(raw)


def pin_gta(raw: dict) -> dict:
    """Pin Gaming and Stunts in a raw accounts.json dict to the days these tests were written on (in place;
    returns raw): no dated "volume" rule, Gaming's ten slots a day and Stunts' six. On 2026-10-29 the live
    two got dated rules (Andrew: "It's 6 to 8 for every account": 6 a day spread from the morning, 8 from the GTA 6
    launch) and Gaming deals "best" slots; test_new_accounts and test_best_of_gta check those against the live config."""
    accts = raw["accounts"]
    if "gaming" in accts:
        g = accts["gaming"]
        g.pop("volume", None)
        g["daily_target"] = len(g["slots"])
        # and its old rotation: the live one deals "best" since 2026-10-29 (Best of GTA, tests/test_best_of_gta.py)
        g["formats"], g["slot_formats"] = ["underlay", "pip"], ["underlay", "pip", "gameplay"]
        # and three clips a source: the live one cuts one a video since 2026-10-31 (Andrew: "just the best clips of
        # the day"; test_best_of_gta checks that against the live config)
        g["clips_per_source"] = 3
    if "stunts" in accts:
        c = accts["stunts"]
        c.pop("volume", None)
        c["daily_target"] = 6
    return raw
