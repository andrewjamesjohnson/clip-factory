import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Inbox's own files (socialstore.py), 2026-10-19: items in the month of their own time, a poll that keeps what we
# did, conversations and their answer window, people and STOP, saved replies, cursors, the posts index, the Part 2
# waitlist, the automations' runs, 45 days kept, and Forget (which also blanks the person in the outward log).
# Throwaway folders; no network.

import json
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import cfg_in_tmp           # noqa: E402

from clipfactory import config as C         # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import socialstore as SS   # noqa: E402
from clipfactory import store               # noqa: E402

NOW = datetime(2031, 7, 8, 15, 0, tzinfo=timezone.utc)


def comment(cid: str, at: datetime, who: str = "42", text: str = "source?", **kw) -> dict:
    return {"id": SS.item_id("instagram", cid), "platform": "instagram", "kind": "comment", "platform_comment_id": cid,
            "post": {"platform_id": "1789", "outlet": "instagram_reels", "board": "2031-07-08", "slot": "08:45"},
            "author": {"key": f"instagram:{who}", "id": who, "name": f"user{who}", "is_us": False},
            "text": text, "at": at.isoformat(), "like_count": 0, "hidden": False, **kw}


class SocialStoreTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")

    def test_1_items_live_in_their_own_month_and_a_poll_keeps_what_we_did(self):
        july, june = NOW, NOW - timedelta(days=20)
        new, changed = SS.upsert_items(self.gta, [comment("1", july), comment("2", june)], now=NOW)
        self.assertEqual((new, changed), (["ig:2", "ig:1"], []))
        engage = C.STATE / "gaming" / "engage"
        self.assertEqual(sorted(p.name for p in engage.glob("items-*.json")), ["items-2031-06.json",
                                                                              "items-2031-07.json"])
        self.assertEqual(SS.get_item(self.gta, "ig:1")["state"], "new")
        SS.set_state(self.gta, "ig:1", "answered", "Andrew", now=NOW, answered={"by": "Andrew", "at": NOW.isoformat()})
        SS.add_reply(self.gta, "ig:1", {"id": "r1", "by": "us", "text": "In the first comment.", "at": NOW.isoformat(),
                                        "action": "oa-1"})
        SS.add_reply(self.gta, "ig:1", {"action": "oa-1", "id": "ig:99"})            # the same action: updated
        new, changed = SS.upsert_items(self.gta, [comment("1", july, like_count=3, state="new", replies=[])], now=NOW)
        self.assertEqual((new, changed), ([], ["ig:1"]))
        it = SS.get_item(self.gta, "ig:1")
        self.assertEqual((it["like_count"], it["state"], it["answered"]["by"]), (3, "answered", "Andrew"))
        self.assertEqual([(r["id"], r["text"]) for r in it["replies"]], [("ig:99", "In the first comment.")])
        self.assertEqual(SS.upsert_items(self.gta, [comment("1", july, like_count=3)], now=NOW), ([], []))
        self.assertEqual([i["id"] for i in SS.items(self.gta)], ["ig:1", "ig:2"])         # newest first
        self.assertEqual([i["id"] for i in SS.items(self.gta, since=NOW - timedelta(days=1))], ["ig:1"])
        self.assertEqual([i["id"] for i in SS.items(self.gta, states=["new"])], ["ig:2"])
        self.assertEqual(SS.items(self.gta, platforms=["facebook"]), [])
        with self.assertRaises(SystemExit):
            SS.set_state(self.gta, "ig:1", "loved", "Andrew")
        with self.assertRaises(SystemExit):
            SS.set_state(self.gta, "ig:404", "done", "Andrew")
        self.assertEqual((SS.item_id("facebook", "5", "dm"), SS.thread_id("instagram", "aW")), ("fbm:5", "igt:aW"))
        with self.assertRaises(SystemExit):
            SS.thread_id("youtube", "x")

    def test_1b_unhide_and_our_own_notes_are_public_setters(self):
        """2026-10-20 (the builders' reports): unhide clears hidden, set_local writes only our own notes; a poll keeps
        both, and neither reaches into another month's file by hand."""
        SS.upsert_items(self.gta, [comment("5", NOW - timedelta(days=20))], now=NOW)
        SS.set_state(self.gta, "ig:5", "hidden", "click", now=NOW)
        self.assertTrue(SS.get_item(self.gta, "ig:5")["hidden"])
        it = SS.unhide(self.gta, "ig:5", "click", "answered", now=NOW)
        self.assertEqual((it["hidden"], it["state"], it["state_by"]), (False, "answered", "click"))
        for bad in ("hidden", "gone", "loved"):
            with self.assertRaises(SystemExit):
                SS.unhide(self.gta, "ig:5", "click", bad)
        it = SS.set_local(self.gta, "ig:5", auto={"rule": "r-src", "result": "practice"},
                          suggested_reply={"text": "From the full stream", "why": "source"})
        self.assertEqual(it["auto"]["rule"], "r-src")
        with self.assertRaises(SystemExit):
            SS.set_local(self.gta, "ig:5", state="done")                 # the state goes through set_state
        with self.assertRaises(SystemExit):
            SS.set_local(self.gta, "ig:404", auto={})
        SS.upsert_items(self.gta, [comment("5", NOW - timedelta(days=20), auto=None, suggested_reply=None)], now=NOW)
        it = SS.get_item(self.gta, "ig:5")
        self.assertEqual((it["auto"]["rule"], it["suggested_reply"]["why"], it["hidden"]), ("r-src", "source", False))

    def test_2_threads_keep_twenty_and_open_the_window(self):
        tid = SS.thread_id("instagram", "aW")
        t = {"id": tid, "platform": "instagram", "with": {"key": "instagram:42", "id": "42", "name": "user42"}}
        msgs = [{"id": f"m{i}", "at": (NOW + timedelta(minutes=i)).isoformat(), "from_us": i % 5 == 4,
                 "text": f"hello {i}"} for i in range(25)]
        fresh = SS.upsert_thread(self.gta, t, msgs, now=NOW)
        self.assertEqual(len(fresh), 25)
        got = SS.thread(self.gta, tid)
        self.assertEqual(len(got["messages"]), 20)
        self.assertEqual(got["messages"][-1]["id"], "m24")
        self.assertEqual(got["last_in_at"], (NOW + timedelta(minutes=23)).isoformat())
        self.assertEqual(got["window_until"], (NOW + timedelta(minutes=23, hours=24)).isoformat())
        self.assertEqual((got["state"], got["unread"]), ("new", 20))
        SS.set_thread_state(self.gta, tid, "done", "Andrew", now=NOW)
        self.assertEqual(SS.upsert_thread(self.gta, t, msgs[-3:], now=NOW), [])          # nothing new: stays done
        self.assertEqual(SS.thread(self.gta, tid)["state"], "done")
        more = [{"id": "m99", "at": (NOW + timedelta(hours=1)).isoformat(), "from_us": False, "text": "and?"}]
        self.assertEqual(SS.upsert_thread(self.gta, t, more, now=NOW), ["m99"])
        self.assertEqual((SS.thread(self.gta, tid)["state"], SS.thread(self.gta, tid)["unread"]), ("new", 1))
        self.assertEqual([x["id"] for x in SS.threads(self.gta, states=["new"])], [tid])

    def test_3_contacts_stop_saved_cursor_posts(self):
        key = "instagram:42"
        self.assertFalse(SS.stopped(self.gta, key))
        SS.touch_contact(self.gta, key, NOW - timedelta(days=100), name="user42", outbound=True)
        c = SS.touch_contact(self.gta, key, NOW, inbound=True, auto=True, private_reply="1789")
        self.assertEqual(c["outs"], [NOW.isoformat(timespec="seconds")])                # outs keep 90 days
        self.assertEqual((c["name"], c["last_in_at"], c["private_replied"]), ("user42", NOW.isoformat(), ["1789"]))
        self.assertEqual(c["first_at"], (NOW - timedelta(days=100)).isoformat())
        SS.stop_contact(self.gta, key, NOW, why="STOP")
        self.assertTrue(SS.stopped(self.gta, key))
        SS.stop_contact(self.gta, key, NOW, on=False)
        self.assertFalse(SS.stopped(self.gta, key))
        sid = SS.saved_put(self.gta, "Source", "Full video in the first comment.", ["instagram", "tiktok"], now=NOW)
        self.assertEqual(SS.saved(self.gta)[0]["platforms"], ["instagram"])
        SS.saved_put(self.gta, "Source", "It is in the first comment.", sid=sid, now=NOW)
        self.assertEqual([s["text"] for s in SS.saved(self.gta)], ["It is in the first comment."])
        with self.assertRaises(SystemExit):
            SS.saved_put(self.gta, "", "x")
        self.assertTrue(SS.saved_delete(self.gta, sid))
        self.assertFalse(SS.saved_delete(self.gta, sid))
        SS.set_cursor(self.gta, "instagram", {"threads_at": "a"})
        SS.set_cursor(self.gta, "instagram", {"media": {"1": {"count": 3}}})
        self.assertEqual(SS.cursor(self.gta, "instagram"), {"threads_at": "a", "media": {"1": {"count": 3}}})
        self.assertEqual(SS.cursor(self.gta, "youtube"), {})
        SS.set_posts_index(self.gta, {"1789": {"outlet": "instagram_reels", "day": "2031-07-08", "slot": "08:45"}}, NOW)
        self.assertEqual(SS.posts_index(self.gta)["1789"]["slot"], "08:45")
        with SS.edit(self.gta, "rules") as d:
            d["rules"] = {"r-src": {"id": "r-src"}}
        self.assertEqual(SS.read(self.gta, "rules")["rules"], {"r-src": {"id": "r-src"}})
        with self.assertRaises(SystemExit):
            SS.read(self.gta, "../../secrets")

    def test_4_waitlist_and_runs(self):
        self.assertTrue(SS.waitlist_add(self.gta, "https://y/x", "ig:1", "instagram:42", now=NOW))
        self.assertFalse(SS.waitlist_add(self.gta, "https://y/x", "ig:1", "instagram:42", now=NOW))
        SS.waitlist_add(self.gta, "https://y/z", "ig:2", "instagram:7", now=NOW - timedelta(days=8))
        self.assertEqual([w["item"] for w in SS.waitlist(self.gta, now=NOW)], ["ig:1"])   # the other expired
        self.assertTrue(SS.waitlist_mark(self.gta, "https://y/x", "ig:1", "oa-7"))
        self.assertEqual(SS.waitlist(self.gta, "https://y/x", now=NOW), [])
        self.assertFalse(SS.waitlist_mark(self.gta, "https://y/x", "ig:404", "oa-8"))
        SS.append_run(self.gta, {"rule": "r-src", "item": "ig:1", "result": "practice", "why": ""}, now=NOW)
        SS.append_run(self.gta, {"rule": "r-who", "item": "ig:2", "result": "skipped", "why": "cap",
                                 "at": (NOW - timedelta(days=40)).isoformat()}, now=NOW)
        self.assertEqual(sorted(p.name for p in SS.root(self.gta).glob("runs-*.jsonl")),
                         ["runs-2031-05.jsonl", "runs-2031-07.jsonl"])
        self.assertEqual([r["rule"] for r in SS.runs(self.gta)], ["r-who", "r-src"])
        self.assertEqual([r["rule"] for r in SS.runs(self.gta, rule="r-src")], ["r-src"])

    def test_5_prune_keeps_45_days_and_a_stop(self):
        old, mid = NOW - timedelta(days=60), NOW - timedelta(days=30)
        SS.upsert_items(self.gta, [comment("1", old), comment("2", mid), comment("3", NOW)], now=NOW)
        tid = SS.thread_id("facebook", "t1")
        SS.upsert_thread(self.gta, {"id": tid, "platform": "facebook", "with": {"key": "facebook:9"}},
                         [{"id": "m1", "at": old.isoformat(), "from_us": False, "text": "hi"}], now=old)
        SS.touch_contact(self.gta, "facebook:9", old - timedelta(days=40), inbound=True)
        SS.stop_contact(self.gta, "instagram:5", old - timedelta(days=60), why="STOP")
        SS.waitlist_add(self.gta, "src", "ig:1", "instagram:42", now=old)
        SS.append_run(self.gta, {"rule": "r", "item": "ig:1", "at": (NOW - timedelta(days=100)).isoformat()}, now=NOW)
        SS.append_run(self.gta, {"rule": "r", "item": "ig:3", "at": NOW.isoformat()}, now=NOW)
        gone = SS.prune(self.gta, NOW, keep_days=90)                  # 45 at most
        self.assertEqual([i["id"] for i in SS.items(self.gta)], ["ig:3", "ig:2"])
        self.assertFalse((SS.root(self.gta) / f"items-{old:%Y-%m}.json").exists())
        self.assertIsNone(SS.thread(self.gta, tid))
        self.assertEqual(SS.contact(self.gta, "facebook:9")["first_at"], None)       # gone: a blank record
        self.assertTrue(SS.stopped(self.gta, "instagram:5"))                         # a STOP is kept
        self.assertEqual(SS.waitlist(self.gta, now=NOW), [])
        self.assertEqual([r["item"] for r in SS.runs(self.gta)], ["ig:3"])         # a month file goes whole
        self.assertGreaterEqual(gone, 5)
        self.assertEqual(SS.prune(self.gta, NOW), 0)

    def test_5b_a_run_past_the_days_kept_loses_the_person_though_its_month_stays(self):
        old, mid = NOW - timedelta(days=59), NOW - timedelta(days=50)              # both in runs-2031-05.jsonl
        recent = NOW - timedelta(days=5)
        for at, item in ((old, "ig:9"), (mid, "ig:8"), (recent, "ig:7")):
            SS.append_run(self.gta, {"at": at.isoformat(), "rule": "r", "item": item, "to": "instagram:999",
                                     "name": "pat_ig", "contact": "instagram:999", "result": "practice",
                                     "text": {"reply_public": "@pat_ig it is from MrBossFTW"},
                                     "suggested": "@pat_ig it is from MrBossFTW"}, now=at)
        SS.prune(self.gta, NOW)
        self.assertTrue((SS.root(self.gta) / "runs-2031-05.jsonl").exists())     # its month is not past the cut
        runs = SS.runs(self.gta)
        self.assertEqual([(r["item"], r["rule"], r["result"]) for r in runs],
                         [("ig:9", "r", "practice"), ("ig:8", "r", "practice"), ("ig:7", "r", "practice")])
        may = (SS.root(self.gta) / "runs-2031-05.jsonl").read_text()
        self.assertNotIn("pat_ig", may)
        self.assertNotIn("instagram:999", may)
        self.assertTrue(all(r.get("pruned") for r in runs[:2]))
        self.assertEqual(runs[2]["name"], "pat_ig")                               # inside the days kept: whole
        self.assertEqual(SS.prune(self.gta, NOW), 0)                              # stripped once

    def test_6b_forget_takes_our_replies_under_their_comments_and_keeps_their_stop(self):
        ours = comment("R1", NOW, who="1784", text="@user42 it is from MrBossFTW", kind="reply", parent_id="1")
        ours["author"] = {"key": "instagram:1784", "id": "1784", "name": "gaming", "is_us": True}
        other = comment("R2", NOW, who="7", text="same question", kind="reply", parent_id="1")
        SS.upsert_items(self.gta, [comment("1", NOW), ours, other, comment("3", NOW, who="7")], now=NOW)
        SS.stop_contact(self.gta, "instagram:42", NOW, why=SS.STOP_WHY)
        n = SS.forget(self.gta, "instagram:42", "Andrew", cfg=self.cfg)
        self.assertEqual(n, 3)                                    # their comment, our reply to it, the contact
        self.assertEqual(sorted(i["id"] for i in SS.items(self.gta)), ["ig:3", "ig:R2"])   # others' words stay
        self.assertNotIn("user42", json.dumps(SS.items(self.gta)))
        self.assertTrue(SS.stopped(self.gta, "instagram:42"))
        self.assertEqual(SS.contact(self.gta, "instagram:42")["first_at"], None)       # the record itself went

    def test_6_forget_removes_the_person_here_and_in_the_outward_log(self):
        SS.upsert_items(self.gta, [comment("1", NOW), comment("2", NOW, who="7", text="great clip")], now=NOW)
        tid = SS.thread_id("instagram", "t42")
        SS.upsert_thread(self.gta, {"id": tid, "platform": "instagram", "with": {"key": "instagram:42"}},
                         [{"id": "m1", "at": NOW.isoformat(), "text": "where is the full video"}], now=NOW)
        SS.touch_contact(self.gta, "instagram:42", NOW, name="user42", inbound=True)
        SS.waitlist_add(self.gta, "src", "ig:1", "instagram:42", now=NOW)
        SS.append_run(self.gta, {"rule": "r", "item": "ig:1", "text": "@user42 it is in the first comment"}, now=NOW)
        SS.append_run(self.gta, {"rule": "r", "item": "ig:2", "text": "thanks"}, now=NOW)
        seen = []
        ow = REG.load("outward")                     # the module the plug board hands out (another test may
        orig = ow.forget                             # have imported a fresh copy)

        def forget(cfg, acct, person):
            seen.append((cfg, acct.slug, person))
            return orig(cfg, acct, person)
        ow.forget = forget
        self.addCleanup(setattr, ow, "forget", orig)
        n = SS.forget(self.gta, "instagram:42", "Andrew", cfg=self.cfg)
        self.assertEqual(seen, [(self.cfg, "gaming", "instagram:42")])
        self.assertEqual(n, 5)                   # the comment, the thread, the contact, the waitlist place, the run
        self.assertEqual([i["id"] for i in SS.items(self.gta)], ["ig:2"])
        self.assertIsNone(SS.thread(self.gta, tid))
        self.assertEqual(SS.contact(self.gta, "instagram:42")["name"], "")
        self.assertEqual(SS.waitlist(self.gta, now=NOW), [])
        self.assertEqual([r["text"] for r in SS.runs(self.gta)], ["[forgotten]", "thanks"])
        log = (SS.root(self.gta) / "forgotten.jsonl").read_text()
        self.assertEqual(json.loads(log)["by"], "Andrew")
        self.assertNotIn("42", log.replace(json.loads(log)["at"], ""))           # who it was is not kept
        with self.assertRaises(SystemExit):
            SS.forget(self.gta, "nobody", "Andrew")

    def test_7_each_file_has_its_own_lock_and_stays_in_state(self):
        def touch(i):
            SS.touch_contact(self.gta, f"instagram:{i}", NOW, inbound=True)
        ts = [threading.Thread(target=touch, args=(i,)) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        got = SS.read(self.gta, "contacts")["contacts"]
        self.assertEqual(len(got), 12)                                               # no write lost
        self.assertTrue((SS.root(self.gta) / "contacts.json.lock").exists())
        with store.edit(store.path("gaming", "engage", "threads.json")) as d:     # another file is not held
            SS.touch_contact(self.gta, "instagram:99", NOW)
            d["v"] = 1
        self.assertEqual(SS.root(self.gta), C.STATE / "gaming" / "engage")
        with self.assertRaises(SystemExit):
            SS.edit(self.gta, "items-31").__enter__()


if __name__ == "__main__":
    unittest.main()
