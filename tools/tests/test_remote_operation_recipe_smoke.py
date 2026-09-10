from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.remote_operation import single_host_recipe_smoke as recipe_smoke


class RemoteOperationRecipeSmokeTest(unittest.TestCase):
    def test_cross_host_server_and_client_commands_are_public(self) -> None:
        server = recipe_smoke.parser().parse_args(
            ["server", "--session-id", "cross-host-001"]
        )
        client = recipe_smoke.parser().parse_args(
            ["client", "--session-id", "cross-host-001"]
        )
        self.assertEqual(server.listen_address, "192.168.2.100")
        self.assertEqual(client.server_address, "192.168.2.100")
        self.assertEqual(server.port, client.port)

    def test_recipe_operations_are_a_closed_local_mapping(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                recipe_smoke.subprocess, "run", return_value=completed
            ) as run:
                recipe_smoke._run_recipe(
                    "doctor", recipe_smoke.DEFAULT_EXPERIMENT, Path(temporary)
                )
            command = run.call_args.args[0]

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], str(recipe_smoke.OPERATOR))
        self.assertEqual(command[2], "doctor")
        self.assertNotIn("shell", run.call_args.kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(recipe_smoke.SmokeError, "unsupported"):
                recipe_smoke._run_recipe(
                    "arbitrary-command",
                    recipe_smoke.DEFAULT_EXPERIMENT,
                    Path(temporary),
                )

    def test_peer_identity_rejects_attempt_or_hash_drift(self) -> None:
        message = recipe_smoke._identity(
            experiment=recipe_smoke.DEFAULT_EXPERIMENT,
            session_id="session-001",
            source_host=recipe_smoke.CLIENT_HOST,
            sequence=1,
            kind="status",
            message_type="REGISTERED",
        )
        message["attempt"] = 2
        with self.assertRaisesRegex(recipe_smoke.SmokeError, "attempt"):
            recipe_smoke._check_peer_message(
                message,
                experiment=recipe_smoke.DEFAULT_EXPERIMENT,
                session_id="session-001",
                source_host=recipe_smoke.CLIENT_HOST,
                expected_sequence=1,
                kind="status",
            )


if __name__ == "__main__":
    unittest.main()
