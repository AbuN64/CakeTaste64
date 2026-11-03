import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from cake_taste import TasteError, cli


class StageDurationTests(unittest.TestCase):
    def test_public_command_preserves_seconds_and_completes_on_success_and_failure(self):
        for failure in (None, TasteError("probe.failed", "broken")):
            events = []

            @contextlib.contextmanager
            def timed(name, events=events):
                events.append("open")
                try:
                    yield SimpleNamespace(seconds=9.87654321)
                finally:
                    events.append("closed")

            domain = mock.Mock(return_value={"value": 7}, side_effect=failure)
            output = io.StringIO()
            with (
                mock.patch.object(cli.cache_stages, "timed", timed),
                mock.patch.object(cli, "_profile", return_value=object()),
                mock.patch.object(cli, "_domain", domain),
                contextlib.redirect_stdout(output),
            ):
                code = cli.main(["--profile", "seat.json", "media"])
            body = json.loads(output.getvalue())
            self.assertEqual(code, 0 if failure is None else 1)
            self.assertEqual(body["stages"], [{"name": "media", "wall_seconds": 9.876543}])
            self.assertEqual(events, ["open", "closed"])
            if failure:
                self.assertEqual(body["findings"][0]["key"], "probe.failed")
