"""ensure_runtime：启动连接失败时自动重试一次（间隔 3 秒）。"""

import unittest
from unittest.mock import patch


def _config(**overrides):
    browser = {"runtime": "builtin", "auto_start_proxy": True, "proxy_host": "127.0.0.1", "proxy_port": 3456}
    browser.update(overrides)
    return {"browser": browser}


class EnsureRuntimeRetryTests(unittest.TestCase):
    def _patches(self, testcase):
        patches = [
            patch("bosshunter.browser.runtime.time.sleep"),
            patch("bosshunter.browser.runtime._is_port_available", return_value=True),
            patch("bosshunter.browser.runtime.runtime_health", return_value=None),
            patch("bosshunter.browser.runtime.runtime_targets"),
            patch("bosshunter.browser.runtime.start_runtime"),
            patch("bosshunter.browser.runtime.check_node_available",
                  return_value={"available": True, "version": "v22.1.0"}),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            testcase.addCleanup(p.stop)
        # 返回顺序与 patches 列表一致：sleep, port, health, targets, start, node
        return mocks

    def test_failure_retries_once_after_three_seconds(self):
        sleep, _port, _health, targets, start, _node = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        targets.return_value = None
        result = ensure_runtime(_config(), wait_seconds=0)

        self.assertFalse(result)
        self.assertEqual(start.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_retry_success_recovers(self):
        sleep, _port, _health, targets, start, _node = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        calls = {"n": 0}

        def _targets(browser=None):
            calls["n"] += 1
            # 第 1 次尝试：初始检查与启动后探测都失败；
            # 第 2 次尝试（重试）：启动后探测成功。
            return [{"targetId": "abc"}] if calls["n"] == 4 else None

        targets.side_effect = _targets
        result = ensure_runtime(_config(), wait_seconds=0)

        self.assertTrue(result)
        self.assertEqual(start.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_success_on_first_attempt_does_not_retry(self):
        sleep, _port, _health, targets, start, _node = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        targets.side_effect = [None, [{"targetId": "abc"}]]
        result = ensure_runtime(_config(), wait_seconds=0)

        self.assertTrue(result)
        start.assert_called_once()
        sleep.assert_not_called()

    def test_already_running_runtime_does_not_retry(self):
        sleep, _port, _health, targets, start, _node = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        targets.return_value = [{"targetId": "abc"}]
        result = ensure_runtime(_config(), wait_seconds=0)

        self.assertTrue(result)
        start.assert_not_called()
        sleep.assert_not_called()

    def test_auto_start_disabled_does_not_retry(self):
        sleep, _port, _health, targets, start, _node = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        targets.return_value = None
        result = ensure_runtime(_config(auto_start_proxy=False), wait_seconds=0)

        self.assertFalse(result)
        start.assert_not_called()
        sleep.assert_not_called()

    def test_non_builtin_runtime_does_not_retry(self):
        sleep, _port, _health, targets, start, _node = self._patches(self)
        from bosshunter.browser.runtime import ensure_runtime

        targets.return_value = None
        result = ensure_runtime(_config(runtime="external"), wait_seconds=0)

        self.assertFalse(result)
        start.assert_not_called()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
