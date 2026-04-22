from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from urllib import error


MODULE_PATH = Path(__file__).resolve().parents[1] / "hooks" / "export_chat_hook.py"
SPEC = importlib.util.spec_from_file_location("export_chat_hook", MODULE_PATH)
HOOK = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(HOOK)


class FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ExportChatHookBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_urlopen = HOOK.request.urlopen

    def tearDown(self) -> None:
        HOOK.request.urlopen = self.original_urlopen

    def test_submit_transcript_to_bridge_success(self) -> None:
        def fake_urlopen(req, timeout=0):
            del req, timeout
            return FakeResponse(
                200,
                '{"ok": true, "remote_export_path": "/srv/cache/transcript.txt", "remote_full_export_path": "/srv/cache/transcript.full.raw"}',
            )

        HOOK.request.urlopen = fake_urlopen
        result = HOOK.submit_transcript_to_bridge({"wing": "demo"})
        self.assertTrue(result)

    def test_submit_transcript_to_bridge_unavailable(self) -> None:
        def fake_urlopen(req, timeout=0):
            del req, timeout
            raise error.URLError("connection refused")

        HOOK.request.urlopen = fake_urlopen
        result = HOOK.submit_transcript_to_bridge({"wing": "demo"})
        self.assertFalse(result)

    def test_submit_transcript_to_bridge_malformed_response(self) -> None:
        def fake_urlopen(req, timeout=0):
            del req, timeout
            return FakeResponse(200, '{"ok": true}')

        HOOK.request.urlopen = fake_urlopen
        result = HOOK.submit_transcript_to_bridge({"wing": "demo"})
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()