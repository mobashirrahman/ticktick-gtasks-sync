import base64
import hashlib
import io
import os
import stat
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from ttgtsync import oauth, util


class SecretsFileTests(unittest.TestCase):
    def test_secrets_file_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "secrets.json")
            old = os.umask(0o022)
            try:
                oauth.Secrets(path).update("google", refresh_token="r")
            finally:
                os.umask(old)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertFalse(os.path.exists(f"{path}.tmp"))

    def test_stale_tmp_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "secrets.json")
            with open(f"{path}.tmp", "w", encoding="utf-8") as handle:
                handle.write("junk")
            oauth.Secrets(path).update("ticktick", access_token="a")
            self.assertEqual(oauth.Secrets(path).entry("ticktick")["access_token"], "a")


class AuthorizeUrlTests(unittest.TestCase):
    def test_state_is_random(self):
        self.assertNotEqual(oauth.new_state(), oauth.new_state())
        self.assertGreaterEqual(len(oauth.new_state()), 32)

    def test_google_url_carries_state_and_pkce(self):
        verifier, challenge = oauth.new_pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)
        url = oauth.authorize_url_google("cid", "http://localhost:8080/callback", "st", challenge)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["state"], ["st"])
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


class RetryTests(unittest.TestCase):
    def _run(self, method, side_effect):
        sleeps = []
        with mock.patch("urllib.request.urlopen", side_effect=side_effect) as urlopen:
            result = util.request(method, "https://x", body={} if method == "POST" else None,
                                  sleeper=sleeps.append)
        return result, urlopen.call_count

    def test_post_is_not_retried_on_5xx(self):
        result, calls = self._run("POST", [_http_error(503)] * 4)
        self.assertEqual((result.status, calls), (503, 1))

    def test_post_is_not_retried_on_network_error(self):
        result, calls = self._run("POST", [urllib.error.URLError("timeout")] * 4)
        self.assertEqual((result.status, calls), (0, 1))

    def test_post_is_retried_on_429(self):
        result, calls = self._run("POST", [_http_error(429)] * 4)
        self.assertEqual((result.status, calls), (429, 4))

    def test_patch_is_retried_on_5xx(self):
        result, calls = self._run("PATCH", [_http_error(503)] * 4)
        self.assertEqual((result.status, calls), (503, 4))


if __name__ == "__main__":
    unittest.main()
