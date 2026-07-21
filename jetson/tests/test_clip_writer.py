import datetime
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from jetson.clip_writer import (
    ClipBusy,
    ClipGone,
    ClipNotReady,
    ClipWriter,
    CommandConflict,
    CommandExpired,
    FrameRing,
)


PERIOD_NS = 100000000
BOOT_ID = "0123456789abcdef0123456789abcdef"
BASE_WALL = 1784520000.0


def utc(value):
    return (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=value)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FakeClock(object):
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class FakeEncoder(object):
    def __init__(self, fail=False, bytes_per_frame=1):
        self.fail = fail
        self.bytes_per_frame = bytes_per_frame
        self.calls = []

    def __call__(self, frames, partial_path):
        frames = tuple(frames)
        self.calls.append((frames, partial_path))
        if self.fail:
            raise RuntimeError("secret encoder detail")
        with open(partial_path, "wb") as handle:
            handle.write(b"m" * max(1, len(frames) * self.bytes_per_frame))
        return {
            "width": 640,
            "height": 480,
            "frame_count": len(frames),
            "frame_rate": "10/1",
            "duration_seconds": len(frames) / 10.0,
            "video_codec": "h264",
            "pixel_format": "yuv420p",
        }

    def abort(self):
        return None


class BlockingEncoder(FakeEncoder):
    def __init__(self):
        FakeEncoder.__init__(self)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, frames, partial_path):
        self.started.set()
        if not self.release.wait(2.0):
            raise RuntimeError("encoder timeout")
        return FakeEncoder.__call__(self, frames, partial_path)

    def abort(self):
        self.release.set()


class FrameRingTest(unittest.TestCase):
    def test_ring_is_strict_exact_and_memory_only(self):
        ring = FrameRing()
        with tempfile.TemporaryDirectory() as directory:
            before = set(os.listdir(directory))
            for bucket in range(1, 102):
                ring.push(bucket, ("jpeg-{}".format(bucket)).encode("ascii"))
            self.assertEqual(ring.buckets, tuple(range(2, 102)))
            self.assertEqual(ring.frame_count, 100)
            self.assertEqual(set(os.listdir(directory)), before)
        with self.assertRaisesRegex(ValueError, "^invalid_bucket$"):
            ring.push(101, b"duplicate")
        with self.assertRaisesRegex(ValueError, "^invalid_bucket$"):
            ring.push(100, b"older")
        with self.assertRaisesRegex(ValueError, "^invalid_jpeg$"):
            ring.push(102, b"")

    def test_snapshot_requires_exact_100_preceding_buckets(self):
        ring = FrameRing()
        for bucket in list(range(1, 50)) + list(range(51, 102)):
            ring.push(bucket, b"jpeg")
        with self.assertRaisesRegex(ValueError, "^insufficient_preroll$"):
            ring.snapshot(102)


class ClipWriterTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.clock = FakeClock(BASE_WALL)
        self.monotonic = FakeClock(1000 * PERIOD_NS)
        self.encoder = FakeEncoder()
        self.writer = ClipWriter(self.temporary.name, self.encoder, self.clock, self.monotonic, boot_id=BOOT_ID)

    def tearDown(self):
        self.writer.shutdown()
        self.temporary.cleanup()

    def fill_pre(self, trigger=1000):
        for bucket in range(trigger - 100, trigger):
            self.writer.push(bucket, ("jpeg-{}".format(bucket)).encode("ascii"))

    def put(self, command="a" * 32, digest="1" * 64, trigger=1000, event_type="eating", event_id=41,
            wall=BASE_WALL, committed=None):
        committed = utc(wall) if committed is None else committed
        return self.writer.put(command, digest, committed, event_type, event_id, utc(wall - 30),
                               wall, trigger * PERIOD_NS)

    def finish(self, start=1000, end=1200):
        for bucket in range(start, end):
            self.writer.push(bucket, ("jpeg-{}".format(bucket)).encode("ascii"))
        self.assertTrue(self.writer.wait_idle(2.0))

    def test_default_clip_has_exact_receipt_pre_and_post_frames(self):
        self.fill_pre()
        receipt = self.put()
        self.assertEqual(receipt, {
            "accepted_boot_id": BOOT_ID,
            "command_id": "a" * 32,
            "state": "recording",
            "accepted_at": utc(BASE_WALL),
        })
        partials = [name for name in os.listdir(self.temporary.name) if name.endswith(".partial.mp4")]
        self.assertEqual(len(partials), 1)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.temporary.name, partials[0])).st_mode), 0o600)
        self.finish()
        self.assertEqual(len(self.encoder.calls), 1)
        frames = self.encoder.calls[0][0]
        self.assertEqual(len(frames), 300)
        self.assertEqual(tuple(bucket for bucket, unused in frames), tuple(range(900, 1200)))
        media = self.writer.get("a" * 32)
        self.assertEqual(media["frame_count"], 300)
        self.assertEqual(media["events"], "eating:41")
        self.assertEqual(self.writer.get("a" * 32), media)

    def test_admission_uses_socket_wall_and_ceil_monotonic_bucket_boundaries(self):
        self.fill_pre(1001)
        receipt = self.writer.put("a" * 32, "1" * 64, utc(BASE_WALL + 0.2), "eating", 41,
                                  utc(BASE_WALL - 30), BASE_WALL, 1000 * PERIOD_NS + 1)
        self.assertEqual(receipt["accepted_at"], utc(BASE_WALL))
        self.clock.value += 100.0
        self.assertEqual(self.put(command="a" * 32, wall=BASE_WALL + 100.0), receipt)

        for index, delta in enumerate((-0.2, 2.8, -0.200001, 2.800001)):
            other = ClipWriter(os.path.join(self.temporary.name, "age-{}".format(index)), FakeEncoder(),
                               self.clock, self.monotonic, boot_id=BOOT_ID)
            try:
                trigger = 2000
                for bucket in range(trigger - 100, trigger):
                    other.push(bucket, b"jpeg")
                if delta in (-0.2, 2.8):
                    other.put(("%032x" % trigger)[-32:], ("%064x" % trigger)[-64:], utc(BASE_WALL - delta),
                              "eating", trigger, utc(BASE_WALL - 30), BASE_WALL, trigger * PERIOD_NS)
                else:
                    with self.assertRaisesRegex(CommandExpired, "^command_expired$"):
                        other.put(("%032x" % trigger)[-32:], ("%064x" % trigger)[-64:], utc(BASE_WALL - delta),
                                  "eating", trigger, utc(BASE_WALL - 30), BASE_WALL, trigger * PERIOD_NS)
            finally:
                other.shutdown()

    def test_replay_is_immutable_conflict_precedes_gone_and_delete_is_coalesced(self):
        self.fill_pre()
        first = self.put()
        self.assertEqual(self.put(), first)
        with self.assertRaisesRegex(CommandConflict, "^command_conflict$"):
            self.put(digest="2" * 64)

        second = self.put(command="b" * 32, digest="3" * 64, trigger=1150,
                          event_type="resting", event_id=7, wall=BASE_WALL + 15)
        self.assertEqual(second["state"], "recording")
        self.finish(end=1350)
        media = self.writer.get("b" * 32)
        self.assertEqual(media["frame_count"], 450)
        self.assertEqual(media["events"], "eating:41,resting:7")
        path = media["path"]
        self.writer.delete("b" * 32)
        self.assertFalse(os.path.exists(path))
        for command in ("a" * 32, "b" * 32):
            with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
                self.writer.get(command)
        with self.assertRaisesRegex(CommandConflict, "^command_conflict$"):
            self.put(digest="2" * 64)
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.put()

    def test_clip_duration_limit_and_single_active_clip_do_not_mutate(self):
        self.fill_pre()
        self.put()
        for index, trigger in enumerate((1190, 1380, 1570, 1760, 1900), 2):
            self.put(command=("%032x" % index), digest=("%064x" % index), trigger=trigger,
                     event_id=index, wall=BASE_WALL + (trigger - 1000) / 10.0)
        with self.assertRaisesRegex(ClipBusy, "^clip_busy$"):
            self.put(command="f" * 32, digest="f" * 64, trigger=1901, event_id=99,
                     wall=BASE_WALL + 90.1)
        with self.assertRaisesRegex(ClipBusy, "^clip_busy$"):
            self.put(command="e" * 32, digest="e" * 64, trigger=2200, event_id=100,
                     wall=BASE_WALL + 120)
        self.finish(end=2100)
        self.assertEqual(self.writer.get("a" * 32)["frame_count"], 1200)

    def test_trigger_at_exclusive_end_is_busy_without_mutation(self):
        self.fill_pre()
        self.put()
        count = self.writer.command_count
        with self.assertRaisesRegex(ClipBusy, "^clip_busy$"):
            self.put(command="b" * 32, digest="b" * 64, trigger=1200, event_id=42,
                     wall=BASE_WALL + 20)
        self.assertEqual(self.writer.command_count, count)

    def test_not_ready_encoder_failure_and_shutdown_leave_gone_tombstones(self):
        self.fill_pre()
        self.put()
        with self.assertRaisesRegex(ClipNotReady, "^clip_not_ready$"):
            self.writer.get("a" * 32)
        self.writer.shutdown()
        self.writer = ClipWriter(self.temporary.name, self.encoder, self.clock, self.monotonic, boot_id="f" * 32)
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.put()
        self.assertFalse(any(name.endswith(".partial.mp4") for name in os.listdir(self.temporary.name)))

        failed_dir = os.path.join(self.temporary.name, "failed")
        failed = ClipWriter(failed_dir, FakeEncoder(fail=True), self.clock, self.monotonic, boot_id=BOOT_ID)
        try:
            for bucket in range(900, 1000):
                failed.push(bucket, b"jpeg")
            failed.put("c" * 32, "c" * 64, utc(BASE_WALL), "eating", 1, utc(BASE_WALL - 1),
                       BASE_WALL, 1000 * PERIOD_NS)
            for bucket in range(1000, 1200):
                failed.push(bucket, b"jpeg")
            self.assertTrue(failed.wait_idle(2.0))
            self.assertEqual(failed.last_error, "internal_error")
            self.assertFalse(any(name.endswith(".partial.mp4") for name in os.listdir(failed_dir)))
            with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
                failed.put("c" * 32, "c" * 64, utc(BASE_WALL), "eating", 1,
                           utc(BASE_WALL - 1), BASE_WALL, 1000 * PERIOD_NS)
        finally:
            failed.shutdown()

    def test_finalizing_encoder_does_not_block_replay_or_sampler(self):
        self.writer.shutdown()
        encoder = BlockingEncoder()
        self.writer = ClipWriter(self.temporary.name, encoder, self.clock, self.monotonic, boot_id=BOOT_ID)
        self.fill_pre()
        receipt = self.put()
        self.finish(end=1199)
        final_push = threading.Thread(target=lambda: self.writer.push(1199, b"last"))
        final_push.start()
        self.assertTrue(encoder.started.wait(1.0))
        replay_done = threading.Event()
        sampler_done = threading.Event()
        replay_result = []

        def replay():
            replay_result.append(self.put())
            replay_done.set()

        replay_thread = threading.Thread(target=replay)
        sampler_thread = threading.Thread(target=lambda: (self.writer.push(1200, b"next"), sampler_done.set()))
        replay_thread.start()
        sampler_thread.start()
        try:
            self.assertTrue(replay_done.wait(0.25))
            self.assertTrue(sampler_done.wait(0.25))
            self.assertEqual(replay_result, [receipt])
        finally:
            encoder.release.set()
            final_push.join(2.0)
            replay_thread.join(2.0)
            sampler_thread.join(2.0)
            self.assertTrue(self.writer.wait_idle(2.0))

    def test_shutdown_aborts_blocked_encoder_and_removes_partial(self):
        self.writer.shutdown()
        encoder = BlockingEncoder()
        self.writer = ClipWriter(self.temporary.name, encoder, self.clock, self.monotonic, boot_id=BOOT_ID)
        self.fill_pre()
        self.put()
        self.finish(end=1199)
        self.writer.push(1199, b"last")
        self.assertTrue(encoder.started.wait(1.0))
        started = time.monotonic()
        self.writer.shutdown()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(any(name.endswith(".partial.mp4") for name in os.listdir(self.temporary.name)))
        self.writer = ClipWriter(self.temporary.name, self.encoder, self.clock, self.monotonic, boot_id="f" * 32)
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.writer.put("a" * 32, "1" * 64, utc(BASE_WALL), "eating", 41,
                            utc(BASE_WALL - 30), BASE_WALL, 1000 * PERIOD_NS)

    def test_publish_failure_removes_renamed_media_and_tombstones(self):
        self.fill_pre()
        self.put()
        with mock.patch("jetson.clip_writer._secure_permissions", side_effect=ValueError("invalid_permissions")):
            self.finish()
        self.assertEqual(self.writer.last_error, "internal_error")
        self.assertFalse(any(name.endswith(".mp4") for name in os.listdir(self.temporary.name)))
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.put()

    def test_directory_fsync_failure_still_tombstones_without_sticking_finalizing(self):
        self.fill_pre()
        self.put()
        with mock.patch.object(self.writer, "_sync_directory", side_effect=OSError("fsync failed")):
            self.finish()
        self.assertEqual(self.writer.last_error, "internal_error")
        self.assertFalse(any(name.endswith(".mp4") for name in os.listdir(self.temporary.name)))
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.put()

    def test_posix_owner_only_setup_fails_closed_when_chmod_fails(self):
        self.writer.shutdown()
        locked = os.path.join(self.temporary.name, "locked")
        created = None
        try:
            with mock.patch("jetson.clip_writer.os.name", "posix"), \
                    mock.patch("jetson.clip_writer.os.chmod", side_effect=OSError("denied")):
                with self.assertRaisesRegex(ValueError, "^invalid_permissions$"):
                    created = ClipWriter(locked, self.encoder, self.clock, self.monotonic, boot_id=BOOT_ID)
        finally:
            if created is not None:
                created.shutdown()
        self.writer = ClipWriter(os.path.join(self.temporary.name, "replacement"), self.encoder,
                                 self.clock, self.monotonic, boot_id=BOOT_ID)

    def test_ready_capacity_expiry_restart_and_tombstone_pruning(self):
        for number, trigger in ((1, 1000), (2, 1300)):
            if number == 1:
                self.fill_pre(trigger)
            else:
                for bucket in range(trigger - 100, trigger):
                    self.writer.push(bucket, b"jpeg")
            self.put(command=("%032x" % number), digest=("%064x" % number), trigger=trigger,
                     event_id=number, wall=BASE_WALL + (trigger - 1000) / 10.0)
            self.finish(trigger, trigger + 200)
        for bucket in range(1500, 1600):
            self.writer.push(bucket, b"jpeg")
        with self.assertRaisesRegex(ClipBusy, "^clip_busy$"):
            self.put(command="3" * 32, digest="3" * 64, trigger=1600, event_id=3, wall=BASE_WALL + 60)

        self.clock.value = BASE_WALL + 3600
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.writer.get("%032x" % 1)
        self.put(command="3" * 32, digest="3" * 64, trigger=1600, event_id=3, wall=BASE_WALL + 3600)

        self.writer.shutdown()
        self.writer = ClipWriter(self.temporary.name, self.encoder, self.clock, self.monotonic, boot_id="f" * 32)
        self.clock.value += 86400.001
        self.writer.prune()
        self.assertLessEqual(self.writer.command_count, 1024)

    def test_ready_survives_restart_but_tampered_media_becomes_gone(self):
        self.fill_pre()
        receipt = self.put()
        self.finish()
        path = self.writer.get("a" * 32)["path"]
        self.writer.shutdown()
        self.writer = ClipWriter(self.temporary.name, self.encoder, self.clock, self.monotonic, boot_id="f" * 32)
        self.assertEqual(self.writer.put("a" * 32, "1" * 64, utc(BASE_WALL + 100), "eating", 41,
                                         utc(BASE_WALL - 30), BASE_WALL + 100, 2000 * PERIOD_NS), receipt)
        self.assertEqual(self.writer.get("a" * 32)["path"], path)
        self.writer.shutdown()
        with open(path, "r+b") as handle:
            handle.seek(0)
            handle.write(b"x")
        self.writer = ClipWriter(self.temporary.name, self.encoder, self.clock, self.monotonic, boot_id="e" * 32)
        with self.assertRaisesRegex(ClipGone, "^clip_gone$"):
            self.writer.get("a" * 32)

    def test_command_rows_stop_at_1024_and_old_tombstones_prune_after_boundary(self):
        self.fill_pre()
        self.put()
        self.finish()
        self.writer.delete("a" * 32)
        clip_id = self.writer._db.execute("SELECT id FROM clips").fetchone()[0]
        original = self.writer._db.execute("SELECT * FROM commands WHERE command_id=?", ("a" * 32,)).fetchone()
        with self.writer._transaction():
            for number in range(2, 1025):
                self.writer._db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("{:032x}".format(number), "{:064x}".format(number), clip_id, "delivered",
                     original["accepted_boot_id"], original["accepted_at"], original["accepted_monotonic_ns"],
                     original["trigger_bucket"], "recording", BASE_WALL),
                )
        self.assertEqual(self.writer.command_count, 1024)
        with self.assertRaisesRegex(ClipBusy, "^clip_busy$"):
            self.writer.put("f" * 32, "f" * 64, utc(BASE_WALL), "eating", 99, utc(BASE_WALL - 1),
                            BASE_WALL, 1200 * PERIOD_NS)
        self.clock.value = BASE_WALL + 86400
        self.writer.prune()
        self.assertEqual(self.writer.command_count, 1024)
        self.clock.value += 0.001
        self.writer.prune()
        self.assertEqual(self.writer.command_count, 0)
        self.writer.put("f" * 32, "f" * 64, utc(self.clock.value), "eating", 99,
                        utc(self.clock.value - 1), self.clock.value, 1200 * PERIOD_NS)


if __name__ == "__main__":
    unittest.main()
