

# The public copy leaves out the private files (accounts' real setup, creator roster, docs) these tests read.
PRIVATE_ONLY = ['tests/test_factory.py::FactoryTest::test_8_stats_match_videos_to_clips_and_ids_match_the_dashboard', 'tests/test_gta_maker_fixes.py::Person::test_feed_writes_the_person_as_the_inbox_name', 'tests/test_gta_maker_fixes.py::Person::test_scout_config_names_the_face_behind_each_channel', 'tests/test_learn_morning.py::WordsTest::test_the_runbook_line_in_every_choosing_runbook']


def pytest_collection_modifyitems(config, items):
    import pytest
    for item in items:
        if item.nodeid in PRIVATE_ONLY:
            item.add_marker(pytest.mark.skip(reason="needs the private files"))
