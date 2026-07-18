import datetime as dt
import unittest

from integration_security import oauth_state_is_fresh, safe_return_url


class IntegrationSecurityTests(unittest.TestCase):
    def test_return_url_must_match_the_frontend_origin(self):
        frontend = "https://app.example.com/"

        self.assertEqual(
            safe_return_url("https://app.example.com/planner", frontend),
            "https://app.example.com/planner",
        )
        self.assertEqual(
            safe_return_url("https://attacker.example/collect", frontend),
            frontend,
        )

    def test_oauth_state_expires_after_ten_minutes(self):
        now = dt.datetime(2026, 7, 18, 12, 0, tzinfo=dt.timezone.utc)

        self.assertTrue(
            oauth_state_is_fresh("2026-07-18T11:55:00Z", now=now)
        )
        self.assertFalse(
            oauth_state_is_fresh("2026-07-18T11:49:59Z", now=now)
        )
        self.assertFalse(oauth_state_is_fresh("not-a-date", now=now))


if __name__ == "__main__":
    unittest.main()
