from unittest import TestCase

from bosshunter.collection.capabilities import platform_supports


class CollectionOnlyPlatformCapabilityTests(TestCase):
    def test_new_platforms_support_delivery_and_monitor(self):
        for platform in ("zhilian", "51job", "liepin"):
            for capability in ("collect", "score", "greet", "deliver", "monitor"):
                self.assertTrue(platform_supports(platform, capability))
