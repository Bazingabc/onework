import json
import tempfile
import unittest
from pathlib import Path

from agent_views.bridge.hook_auth import HookTokenStore
from agent_views.hooks.forward_event import sanitize_event
from agent_views.hooks.install import EVENTS, install, uninstall


class HookSupportTest(unittest.TestCase):
    def test_token_store_round_trip_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "hook-token"
            first = HookTokenStore(path).load_or_create()
            second = HookTokenStore(path).load_or_create()

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_forwarder_keeps_only_bounded_status_fields(self):
        result = sanitize_event(
            {
                "hook_event_name": "Stop",
                "session_id": "thread-1",
                "turn_id": "turn-1",
                "last_assistant_message": "请确认" * 5_000,
                "transcript_path": "/secret/transcript.jsonl",
                "tool_input": {"token": "secret"},
            }
        )

        self.assertEqual(result["session_id"], "thread-1")
        self.assertLessEqual(len(result["last_assistant_message"]), 8_000)
        self.assertNotIn("transcript_path", result)
        self.assertNotIn("tool_input", result)

    def test_installer_merges_idempotently_and_uninstalls_only_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            state_home = root / "state"
            codex_home.mkdir()
            config = codex_home / "hooks.json"
            config.write_text(
                json.dumps(
                    {
                        "description": "existing",
                        "hooks": {
                            "Stop": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python3 existing.py",
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            source = Path(__file__).resolve().parents[1] / "forward_event.py"

            first = install(codex_home, state_home, source)
            second = install(codex_home, state_home, source)
            payload = json.loads(config.read_text(encoding="utf-8"))

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(set(payload["hooks"]), set(EVENTS))
            self.assertEqual(len(payload["hooks"]["Stop"]), 2)
            self.assertEqual(payload["description"], "existing")
            handlers = [
                handler
                for group in payload["hooks"]["Stop"]
                for handler in group.get("hooks", [])
                if handler.get("statusMessage") == "Agent Views 同步状态"
            ]
            self.assertTrue(handlers)
            self.assertTrue(all("async" not in handler for handler in handlers))

            removed = uninstall(codex_home, state_home)
            after = json.loads(config.read_text(encoding="utf-8"))
            self.assertTrue(removed["changed"])
            self.assertEqual(list(after["hooks"]), ["Stop"])
            self.assertEqual(
                after["hooks"]["Stop"][0]["hooks"][0]["command"],
                "python3 existing.py",
            )


if __name__ == "__main__":
    unittest.main()
