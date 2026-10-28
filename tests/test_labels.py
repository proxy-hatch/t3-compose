import unittest

from tests.support import OfflineTestCase, ROOT


def label_errors(classes):
    return [cls.__name__ for cls in classes
            if not issubclass(cls, OfflineTestCase) or
            not cls.__name__.startswith(("Offline", "Simulated"))]


def test_classes(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_classes(item)
        else:
            yield type(item)


class OfflineLabels(OfflineTestCase):
    def test_discovered_classes_have_guard_and_label(self):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
        self.assertEqual(label_errors(set(test_classes(suite))), [])

    def test_rejects_missing_guard_and_missing_label(self):
        unguarded = type("OfflineUnguarded", (unittest.TestCase,), {})
        unlabeled = type("Unlabeled", (OfflineTestCase,), {})
        simulated = type("SimulatedQuota", (OfflineTestCase,), {})
        self.assertEqual(label_errors([unguarded, unlabeled, simulated]),
                         ["OfflineUnguarded", "Unlabeled"])
