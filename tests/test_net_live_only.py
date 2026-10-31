import os, tempfile
os.environ["CLIP_FACTORY_OFFLINE"] = "1"
os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp(prefix="clipfactory-net-"))
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import net  # noqa: E402


class LiveOnly(unittest.TestCase):
    def test_a_copy_of_the_state_is_refused_even_when_tests_and_offline_would_allow(self):
        keep = {k: os.environ.get(k) for k in ("CLIP_FACTORY_OFFLINE", "CLIP_FACTORY_ALLOW_NETWORK_IN_TESTS",
                                               "CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY")}
        try:
            os.environ.pop("CLIP_FACTORY_OFFLINE", None)
            os.environ["CLIP_FACTORY_ALLOW_NETWORK_IN_TESTS"] = "1"     # only why_not() is called: nothing is sent
            os.environ.pop("CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY", None)
            self.assertEqual(net.why_not(), "not the live state, a copy")
            with self.assertRaises(net.Offline):
                net.guard("an upload")
            os.environ["CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY"] = "1"
            self.assertEqual(net.why_not(), "")
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(net.why_not(), "offline")


if __name__ == "__main__":
    unittest.main()
