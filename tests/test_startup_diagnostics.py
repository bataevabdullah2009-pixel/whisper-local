import contextlib
import io
import json
from queue import Empty
import unittest
from unittest.mock import Mock, patch

from scripts.benchmark_int8 import Worker


class StartupDiagnostics(unittest.TestCase):
    def worker(self):
        worker = Worker.__new__(Worker)
        worker.startup_progress = True
        worker.startup_stage = None
        worker.started = 0
        worker.events = Mock()
        return worker

    def test_only_safe_stage_metadata_is_logged_before_ready(self):
        worker = self.worker()
        worker.events.get.side_effect = [
            json.dumps({"type": "startup_progress", "stage": "warmup_cpp", "gpu": False,
                        "text": "PRIVATE TEXT", "audio": "PRIVATE AUDIO", "path": "PRIVATE PATH"}),
            json.dumps({"type": "ready"})]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(worker.receive(), {"type": "ready"})
        report = json.loads(output.getvalue())
        self.assertEqual(set(report), {"startup_stage", "seconds", "gpu"})
        self.assertEqual(report["startup_stage"], "warmup_cpp")
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertIsNone(worker.startup_stage)

    def test_progress_does_not_extend_timeout_and_error_identifies_safe_stage(self):
        worker = self.worker()
        clock, timeouts = [0], []
        def receive(timeout):
            timeouts.append(timeout)
            if len(timeouts) == 1:
                clock[0] = 150
                return json.dumps({"type": "startup_progress", "stage": "warmup_vad"})
            raise Empty
        worker.events.get.side_effect = receive
        with patch("scripts.benchmark_int8.time.monotonic", side_effect=lambda: clock[0]), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(RuntimeError, "timed out at startup stage warmup_vad"):
            worker.receive()
        self.assertEqual(timeouts, [300, 150])

    def test_queued_progress_cannot_keep_receiver_alive_after_deadline(self):
        worker = self.worker()
        clock = [0]
        def receive(timeout):
            clock[0] = 300
            return json.dumps({"type": "startup_progress", "stage": "create_context"})
        worker.events.get.side_effect = receive
        with patch("scripts.benchmark_int8.time.monotonic", side_effect=lambda: clock[0]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "timed out"):
            worker.receive()
        self.assertEqual(worker.events.get.call_count, 1)

    def test_unrecognized_stage_is_rejected_without_echoing_content(self):
        worker = self.worker()
        worker.events.get.return_value = json.dumps({"type": "startup_progress", "stage": "PRIVATE TEXT"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError) as raised:
            worker.receive()
        self.assertNotIn("PRIVATE", str(raised.exception))
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
