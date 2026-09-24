from __future__ import annotations

import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import security


class MediaCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        security._sign_media_for_window.cache_clear()

    def test_media_signatures_are_stable_within_rotation_window(self) -> None:
        calls: list[str] = []

        def fake_dumps(path: str) -> str:
            calls.append(path)
            return f"token-{len(calls)}"

        with patch.object(security._serializer, "dumps", fake_dumps):
            with patch.object(security.time, "time", return_value=100_000.0):
                first = security.sign_media("posts/photo.jpg")
            with patch.object(security.time, "time", return_value=100_030.0):
                second = security.sign_media("posts/photo.jpg")

        self.assertEqual(first, second)
        self.assertEqual(calls, ["posts/photo.jpg"])

    def test_media_signatures_rotate_before_their_expiry(self) -> None:
        calls = 0

        def fake_dumps(path: str) -> str:
            nonlocal calls
            calls += 1
            return f"{path}-{calls}"

        with patch.object(security._serializer, "dumps", fake_dumps):
            with patch.object(security.time, "time", return_value=100_000.0):
                first = security.sign_media("posts/photo.jpg")
            with patch.object(
                security.time,
                "time",
                return_value=100_000.0 + security.MEDIA_URL_ROTATION_SECONDS,
            ):
                second = security.sign_media("posts/photo.jpg")

        self.assertNotEqual(first, second)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
