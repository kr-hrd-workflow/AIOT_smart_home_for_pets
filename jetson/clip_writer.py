import calendar
import datetime
import hashlib
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation


PERIOD_NS = 100000000
PRE_ROLL_BUCKETS = 100
POST_ROLL_BUCKETS = 200
MAX_CLIP_BUCKETS = 1200
MAX_READY_FILES = 2
MAX_READY_BYTES = 256 * 1024 * 1024
READY_EXPIRY_SECONDS = 3600
TOMBSTONE_SECONDS = 24 * 3600
MAX_COMMAND_ROWS = 1024
ACTIVE_STATES = ("recording", "finalizing", "ready")
GONE_STATES = ("delivered", "expired", "restart_gone")
ALLOWED_EVENTS = frozenset(("eating", "resting", "bed_sensor_mismatch"))
HEX_32 = re.compile(r"^[0-9a-f]{32}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class ClipError(Exception):
    def __init__(self, code):
        self.code = code
        Exception.__init__(self, code)


class CommandConflict(ClipError):
    pass


class CommandExpired(ClipError):
    pass


class ClipBusy(ClipError):
    pass


class ClipNotReady(ClipError):
    pass


class ClipGone(ClipError):
    pass


class FrameRing(object):
    def __init__(self):
        self._frames = deque(maxlen=PRE_ROLL_BUCKETS)

    def push(self, bucket, jpeg):
        if type(bucket) is not int or bucket < 0 or (self._frames and bucket <= self._frames[-1][0]):
            raise ValueError("invalid_bucket")
        if type(jpeg) is not bytes or not jpeg:
            raise ValueError("invalid_jpeg")
        self._frames.append((bucket, jpeg))

    def snapshot(self, through_bucket):
        if type(through_bucket) is not int or through_bucket < PRE_ROLL_BUCKETS:
            raise ValueError("insufficient_preroll")
        frames = tuple(self._frames)
        expected = tuple(range(through_bucket - PRE_ROLL_BUCKETS, through_bucket))
        if len(frames) != PRE_ROLL_BUCKETS or tuple(item[0] for item in frames) != expected:
            raise ValueError("insufficient_preroll")
        return frames

    @property
    def buckets(self):
        return tuple(item[0] for item in self._frames)

    @property
    def frame_count(self):
        return len(self._frames)


def _timestamp_micros(value):
    if type(value) is str:
        if UTC_TIMESTAMP.match(value) is None:
            raise ValueError("invalid_timestamp")
        try:
            parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            raise ValueError("invalid_timestamp")
        return calendar.timegm(parsed.utctimetuple()) * 1000000 + parsed.microsecond
    if type(value) not in (int, float):
        raise ValueError("invalid_timestamp")
    try:
        return int((Decimal(str(value)) * Decimal(1000000)).to_integral_value())
    except (InvalidOperation, OverflowError, ValueError):
        raise ValueError("invalid_timestamp")


def _format_micros(value):
    seconds, micros = divmod(int(value), 1000000)
    instant = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=seconds)
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + ".{:06d}Z".format(micros)


def _secure_permissions(path, mode, directory=False):
    try:
        os.chmod(path, mode)
    except OSError:
        if os.name == "posix":
            raise ValueError("invalid_permissions")
        return
    if os.name != "posix":
        return
    try:
        info = os.lstat(path)
    except OSError:
        raise ValueError("invalid_permissions")
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected_type or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError("invalid_permissions")


class ClipWriter(object):
    def __init__(self, state_dir, encoder, wall_clock=None, monotonic_ns=None, boot_id=None):
        if not callable(encoder) or not callable(getattr(encoder, "abort", None)):
            raise ValueError("invalid_encoder")
        self.state_dir = os.path.abspath(os.fspath(state_dir))
        if os.path.islink(self.state_dir):
            raise ValueError("invalid_state_dir")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        _secure_permissions(self.state_dir, 0o700, directory=True)
        self.encoder = encoder
        self.wall_clock = wall_clock or time.time
        self.monotonic_ns = monotonic_ns or getattr(time, "monotonic_ns", lambda: int(time.monotonic() * 1000000000))
        self.boot_id = boot_id or uuid.uuid4().hex
        if HEX_32.match(self.boot_id) is None:
            raise ValueError("invalid_boot_id")
        self.ring = FrameRing()
        self._lock = threading.RLock()
        self._closed = False
        self._closing = False
        self._active_clip_id = None
        self._active_frames = None
        self._encoder_thread = None
        self._last_error = None
        database = os.path.join(self.state_dir, "commands.sqlite3")
        if not os.path.exists(database):
            descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        else:
            info = os.lstat(database)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ValueError("invalid_database")
        _secure_permissions(database, 0o600)
        self._db = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._create_schema()
        with self._lock:
            self._recover_locked()

    def _create_schema(self):
        self._db.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL,
                start_bucket INTEGER NOT NULL,
                trigger_bucket INTEGER NOT NULL,
                end_bucket INTEGER NOT NULL,
                anchor_wall_micros INTEGER NOT NULL,
                partial_name TEXT,
                media_name TEXT,
                content_sha256 TEXT,
                media_size INTEGER,
                frame_count INTEGER,
                width INTEGER,
                height INTEGER,
                frame_rate TEXT,
                duration_seconds REAL,
                video_codec TEXT,
                pixel_format TEXT,
                finalized_at REAL
            );
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                body_sha256 TEXT NOT NULL,
                clip_id INTEGER NOT NULL REFERENCES clips(id),
                state TEXT NOT NULL,
                accepted_boot_id TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                accepted_monotonic_ns INTEGER NOT NULL,
                trigger_bucket INTEGER NOT NULL,
                receipt_state TEXT NOT NULL,
                tombstoned_at REAL
            );
            CREATE TABLE IF NOT EXISTS events (
                clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                UNIQUE (clip_id, event_type, event_id)
            );
            """
        )

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.rollback()
            raise
        else:
            self._db.commit()

    def _safe_path(self, name):
        if type(name) is not str or os.path.basename(name) != name:
            raise RuntimeError("internal_error")
        path = os.path.abspath(os.path.join(self.state_dir, name))
        if os.path.dirname(path) != self.state_dir:
            raise RuntimeError("internal_error")
        return path

    def _recover_locked(self):
        now = float(self.wall_clock())
        for name in os.listdir(self.state_dir):
            if name.endswith(".partial.mp4"):
                self._unlink(self._safe_path(name))
        with self._transaction():
            self._db.execute(
                "UPDATE commands SET state='restart_gone', tombstoned_at=? WHERE state IN ('recording','finalizing')",
                (now,),
            )
            self._db.execute("UPDATE clips SET state='restart_gone', partial_name=NULL WHERE state IN ('recording','finalizing')")
        referenced = set()
        for clip in self._db.execute("SELECT * FROM clips WHERE state='ready'").fetchall():
            valid = False
            if clip["media_name"]:
                path = self._safe_path(clip["media_name"])
                referenced.add(clip["media_name"])
                try:
                    info = os.lstat(path)
                    valid = stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    valid = valid and 0 < info.st_size <= MAX_READY_BYTES and info.st_size == clip["media_size"]
                    if valid:
                        valid = self._hash_file(path) == clip["content_sha256"]
                except OSError:
                    valid = False
            if not valid:
                self._mark_gone_locked(clip["id"], "restart_gone", now)
        for name in os.listdir(self.state_dir):
            if name.endswith(".mp4") and not name.endswith(".partial.mp4") and name not in referenced:
                self._unlink(self._safe_path(name))
        self._maintenance_locked(now)

    def _validate_command(self, command_id, digest):
        if type(command_id) is not str or HEX_32.match(command_id) is None:
            raise ValueError("invalid_command_id")
        if type(digest) is not str or HEX_64.match(digest) is None:
            raise ValueError("invalid_body_sha256")

    def _receipt(self, command):
        return {
            "accepted_boot_id": command["accepted_boot_id"],
            "command_id": command["command_id"],
            "state": command["receipt_state"],
            "accepted_at": command["accepted_at"],
        }

    def _existing_locked(self, command_id, digest):
        command = self._db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
        if command is None:
            return None
        if command["body_sha256"] != digest:
            raise CommandConflict("command_conflict")
        if command["state"] in GONE_STATES:
            raise ClipGone("clip_gone")
        return self._receipt(command)

    def put(self, command_id, canonical_body_sha256, committed_at, event_type, event_id,
            occurred_at, received_wall_at, received_monotonic_ns):
        with self._lock:
            self._ensure_open()
            self._validate_command(command_id, canonical_body_sha256)
            now = float(self.wall_clock())
            self._maintenance_locked(now)
            existing = self._existing_locked(command_id, canonical_body_sha256)
            if existing is not None:
                return existing
            if self.command_count >= MAX_COMMAND_ROWS:
                raise ClipBusy("clip_busy")
            if type(event_type) is not str or event_type not in ALLOWED_EVENTS:
                raise ValueError("invalid_event_type")
            if type(event_id) is not int or event_id < 0 or event_id > 9223372036854775807:
                raise ValueError("invalid_event_id")
            occurred_micros = _timestamp_micros(occurred_at)
            committed_micros = _timestamp_micros(committed_at)
            received_micros = _timestamp_micros(received_wall_at)
            age = received_micros - committed_micros
            if age < -200000 or age > 2800000:
                raise CommandExpired("command_expired")
            if type(received_monotonic_ns) is not int or received_monotonic_ns < 0 or received_monotonic_ns > 9223372036854775807:
                raise ValueError("invalid_monotonic_ns")
            trigger_bucket = (received_monotonic_ns + PERIOD_NS - 1) // PERIOD_NS
            accepted_at = _format_micros(received_micros)
            occurred_text = _format_micros(occurred_micros)
            active = self._db.execute(
                "SELECT * FROM clips WHERE state IN ('recording','finalizing') ORDER BY id LIMIT 1"
            ).fetchone()
            if active is not None:
                if active["state"] != "recording":
                    raise ClipBusy("clip_busy")
                new_end = trigger_bucket + POST_ROLL_BUCKETS
                if trigger_bucket >= active["end_bucket"] or new_end > active["start_bucket"] + MAX_CLIP_BUCKETS:
                    raise ClipBusy("clip_busy")
                with self._transaction():
                    self._db.execute("UPDATE clips SET end_bucket=? WHERE id=?", (max(active["end_bucket"], new_end), active["id"]))
                    self._db.execute(
                        "INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                        (command_id, canonical_body_sha256, active["id"], "recording", self.boot_id,
                         accepted_at, received_monotonic_ns, trigger_bucket, "recording"),
                    )
                    self._db.execute(
                        "INSERT OR IGNORE INTO events VALUES (?,?,?,?)",
                        (active["id"], event_type, event_id, occurred_text),
                    )
                return self._existing_locked(command_id, canonical_body_sha256)

            ready = self._db.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(media_size),0) AS size FROM clips WHERE state='ready'"
            ).fetchone()
            if ready["count"] >= MAX_READY_FILES or ready["size"] >= MAX_READY_BYTES:
                raise ClipBusy("clip_busy")
            try:
                pre_roll = self.ring.snapshot(trigger_bucket)
            except ValueError:
                raise ClipBusy("clip_busy")
            partial_name = command_id + ".partial.mp4"
            partial_path = self._safe_path(partial_name)
            descriptor = os.open(partial_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            _secure_permissions(partial_path, 0o600)
            try:
                with self._transaction():
                    cursor = self._db.execute(
                        "INSERT INTO clips (state,start_bucket,trigger_bucket,end_bucket,anchor_wall_micros,partial_name) "
                        "VALUES ('recording',?,?,?,?,?)",
                        (trigger_bucket - PRE_ROLL_BUCKETS, trigger_bucket, trigger_bucket + POST_ROLL_BUCKETS,
                         received_micros, partial_name),
                    )
                    clip_id = cursor.lastrowid
                    self._db.execute(
                        "INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                        (command_id, canonical_body_sha256, clip_id, "recording", self.boot_id,
                         accepted_at, received_monotonic_ns, trigger_bucket, "recording"),
                    )
                    self._db.execute("INSERT INTO events VALUES (?,?,?,?)", (clip_id, event_type, event_id, occurred_text))
            except BaseException:
                self._unlink(partial_path)
                raise
            self._active_clip_id = clip_id
            self._active_frames = list(pre_roll)
            return self._existing_locked(command_id, canonical_body_sha256)

    def lookup_receipt(self, command_id, canonical_body_sha256):
        with self._lock:
            self._ensure_open()
            self._validate_command(command_id, canonical_body_sha256)
            self._maintenance_locked(float(self.wall_clock()))
            return self._existing_locked(command_id, canonical_body_sha256)

    def push(self, bucket, jpeg):
        with self._lock:
            self._ensure_open()
            self.ring.push(bucket, jpeg)
            if self._active_clip_id is None:
                return None
            clip = self._db.execute("SELECT * FROM clips WHERE id=?", (self._active_clip_id,)).fetchone()
            expected = self._active_frames[-1][0] + 1
            if bucket != expected or bucket >= clip["end_bucket"]:
                self._fail_active_locked(float(self.wall_clock()))
                raise RuntimeError("internal_error")
            self._active_frames.append((bucket, jpeg))
            if bucket + 1 == clip["end_bucket"]:
                context = self._begin_finalize_locked(clip)
                thread = threading.Thread(target=self._encode_context, args=(context,))
                self._encoder_thread = thread
                thread.start()
            return None

    def _begin_finalize_locked(self, clip):
        clip_id = clip["id"]
        with self._transaction():
            self._db.execute("UPDATE clips SET state='finalizing' WHERE id=?", (clip_id,))
            self._db.execute("UPDATE commands SET state='finalizing' WHERE clip_id=?", (clip_id,))
        context = (clip_id, clip["partial_name"], tuple(self._active_frames))
        self._active_clip_id = None
        self._active_frames = None
        return context

    def _encode_context(self, context):
        clip_id, partial_name, frames = context
        partial_path = self._safe_path(partial_name)
        published_path = None
        try:
            metadata = self.encoder(frames, partial_path)
            self._validate_media(metadata, frames, partial_path)
            size = os.path.getsize(partial_path)
            digest = self._hash_file(partial_path)
            with open(partial_path, "r+b") as handle:
                os.fsync(handle.fileno())
            with self._lock:
                self._ensure_open()
                current = self._db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
                if current is None or current["state"] != "finalizing":
                    self._unlink(partial_path)
                    return
                other_size = self._db.execute(
                    "SELECT COALESCE(SUM(media_size),0) FROM clips WHERE state='ready' AND id<>?", (clip_id,)
                ).fetchone()[0]
                if other_size + size > MAX_READY_BYTES:
                    raise ClipBusy("clip_busy")
                first_command = self._db.execute(
                    "SELECT command_id FROM commands WHERE clip_id=? ORDER BY rowid LIMIT 1", (clip_id,)
                ).fetchone()[0]
                media_name = first_command + ".mp4"
                media_path = self._safe_path(media_name)
                os.replace(partial_path, media_path)
                published_path = media_path
                self._sync_directory()
                _secure_permissions(media_path, 0o600)
                with self._transaction():
                    self._db.execute(
                        "UPDATE clips SET state='ready',partial_name=NULL,media_name=?,content_sha256=?,media_size=?,"
                        "frame_count=?,width=?,height=?,frame_rate=?,duration_seconds=?,video_codec=?,pixel_format=?,finalized_at=? WHERE id=?",
                        (media_name, digest, size, metadata["frame_count"], metadata["width"], metadata["height"],
                         metadata["frame_rate"], metadata["duration_seconds"], metadata["video_codec"],
                         metadata["pixel_format"], float(self.wall_clock()), clip_id),
                    )
                    self._db.execute("UPDATE commands SET state='ready' WHERE clip_id=?", (clip_id,))
                published_path = None
                self._last_error = None
        except ClipBusy:
            with self._lock:
                if not self._closed:
                    if published_path is not None:
                        try:
                            self._unlink(published_path)
                        except OSError:
                            pass
                    self._mark_gone_locked(
                        clip_id, "restart_gone", float(self.wall_clock()), best_effort_cleanup=True
                    )
                    self._last_error = "clip_busy"
        except BaseException:
            with self._lock:
                if not self._closed:
                    if published_path is not None:
                        try:
                            self._unlink(published_path)
                        except OSError:
                            pass
                    self._mark_gone_locked(
                        clip_id, "restart_gone", float(self.wall_clock()), best_effort_cleanup=True
                    )
                    self._last_error = "internal_error"
        finally:
            with self._lock:
                if self._encoder_thread is threading.current_thread():
                    self._encoder_thread = None

    def _validate_media(self, metadata, frames, partial_path):
        required = {"width", "height", "frame_count", "frame_rate", "duration_seconds", "video_codec", "pixel_format"}
        if type(metadata) is not dict or set(metadata) != required:
            raise ValueError("invalid_media")
        frame_count = len(frames)
        if tuple(item[0] for item in frames) != tuple(range(frames[0][0], frames[0][0] + frame_count)):
            raise ValueError("invalid_media")
        if not (PRE_ROLL_BUCKETS + POST_ROLL_BUCKETS <= frame_count <= MAX_CLIP_BUCKETS):
            raise ValueError("invalid_media")
        if (type(metadata["width"]) is not int or metadata["width"] != 640 or
                type(metadata["height"]) is not int or metadata["height"] != 480 or
                type(metadata["frame_count"]) is not int or metadata["frame_count"] != frame_count or
                metadata["frame_rate"] != "10/1" or metadata["video_codec"] != "h264" or
                metadata["pixel_format"] != "yuv420p" or type(metadata["duration_seconds"]) not in (int, float) or
                not math.isfinite(float(metadata["duration_seconds"])) or
                abs(float(metadata["duration_seconds"]) - frame_count / 10.0) > 0.1):
            raise ValueError("invalid_media")
        info = os.lstat(partial_path)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or not (0 < info.st_size <= MAX_READY_BYTES):
            raise ValueError("invalid_media")

    def _fail_active_locked(self, now):
        if self._active_clip_id is None:
            return
        clip = self._db.execute("SELECT * FROM clips WHERE id=?", (self._active_clip_id,)).fetchone()
        if clip is not None and clip["partial_name"]:
            self._unlink(self._safe_path(clip["partial_name"]))
        self._mark_gone_locked(self._active_clip_id, "restart_gone", now)
        self._active_clip_id = None
        self._active_frames = None

    def _mark_gone_locked(self, clip_id, state, now, best_effort_cleanup=False):
        clip = self._db.execute("SELECT media_name,partial_name FROM clips WHERE id=?", (clip_id,)).fetchone()
        if clip is None:
            return
        for name in (clip["media_name"], clip["partial_name"]):
            if name:
                try:
                    self._unlink(self._safe_path(name))
                except OSError:
                    if not best_effort_cleanup:
                        raise
        with self._transaction():
            self._db.execute(
                "UPDATE clips SET state=?,media_name=NULL,partial_name=NULL WHERE id=?", (state, clip_id)
            )
            self._db.execute(
                "UPDATE commands SET state=?,tombstoned_at=? WHERE clip_id=?", (state, now, clip_id)
            )

    def _maintenance_locked(self, now):
        expired = self._db.execute(
            "SELECT id FROM clips WHERE state='ready' AND finalized_at<=?", (now - READY_EXPIRY_SECONDS,)
        ).fetchall()
        for row in expired:
            self._mark_gone_locked(row["id"], "expired", now)
        with self._transaction():
            self._db.execute(
                "DELETE FROM commands WHERE state IN ('delivered','expired','restart_gone') AND tombstoned_at<?",
                (now - TOMBSTONE_SECONDS,),
            )
            self._db.execute("DELETE FROM clips WHERE NOT EXISTS (SELECT 1 FROM commands WHERE commands.clip_id=clips.id)")

    def prune(self):
        with self._lock:
            self._ensure_open()
            self._maintenance_locked(float(self.wall_clock()))

    def get(self, command_id):
        with self._lock:
            self._ensure_open()
            if type(command_id) is not str or HEX_32.match(command_id) is None:
                raise ValueError("invalid_command_id")
            self._maintenance_locked(float(self.wall_clock()))
            command = self._db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if command is None or command["state"] in GONE_STATES:
                raise ClipGone("clip_gone")
            if command["state"] != "ready":
                raise ClipNotReady("clip_not_ready")
            clip = self._db.execute("SELECT * FROM clips WHERE id=?", (command["clip_id"],)).fetchone()
            path = self._safe_path(clip["media_name"])
            events = self._db.execute(
                "SELECT event_type,event_id FROM events WHERE clip_id=? ORDER BY event_type,event_id", (clip["id"],)
            ).fetchall()
            started = clip["anchor_wall_micros"] - PRE_ROLL_BUCKETS * 100000
            ended = clip["anchor_wall_micros"] + (clip["end_bucket"] - clip["trigger_bucket"]) * 100000
            return {
                "path": path,
                "content_length": clip["media_size"],
                "content_sha256": clip["content_sha256"],
                "started_at": _format_micros(started),
                "ended_at": _format_micros(ended),
                "events": ",".join("{}:{}".format(row["event_type"], row["event_id"]) for row in events),
                "frame_count": clip["frame_count"],
                "width": clip["width"],
                "height": clip["height"],
                "frame_rate": clip["frame_rate"],
                "duration_seconds": clip["duration_seconds"],
                "video_codec": clip["video_codec"],
                "pixel_format": clip["pixel_format"],
            }

    def delete(self, command_id):
        with self._lock:
            self._ensure_open()
            if type(command_id) is not str or HEX_32.match(command_id) is None:
                raise ValueError("invalid_command_id")
            self._maintenance_locked(float(self.wall_clock()))
            command = self._db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if command is None or command["state"] in GONE_STATES:
                raise ClipGone("clip_gone")
            if command["state"] != "ready":
                raise ClipNotReady("clip_not_ready")
            self._mark_gone_locked(command["clip_id"], "delivered", float(self.wall_clock()))
            return None

    @property
    def command_count(self):
        return self._db.execute("SELECT COUNT(*) FROM commands").fetchone()[0]

    @property
    def clip_state(self):
        with self._lock:
            self._ensure_open()
            if self._active_clip_id is not None:
                return "recording"
            row = self._db.execute(
                "SELECT state FROM clips WHERE state IN ('finalizing','ready') "
                "ORDER BY CASE state WHEN 'finalizing' THEN 0 ELSE 1 END LIMIT 1"
            ).fetchone()
            return row["state"] if row is not None else "idle"

    @property
    def last_error(self):
        with self._lock:
            return self._last_error

    def wait_idle(self, timeout=None):
        with self._lock:
            thread = self._encoder_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _ensure_open(self):
        if self._closed or self._closing:
            raise RuntimeError("clip_writer_closed")

    def _unlink(self, path):
        try:
            os.unlink(path)
            self._sync_directory()
        except FileNotFoundError:
            pass

    def _sync_directory(self):
        if os.name == "nt":
            return
        descriptor = os.open(self.state_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _hash_file(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    def shutdown(self, timeout=5.0):
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("invalid_timeout")
        with self._lock:
            if self._closed:
                return
            self._closing = True
            thread = self._encoder_thread
        abort_failed = False
        if thread is not None and thread is not threading.current_thread():
            try:
                self.encoder.abort()
            except BaseException:
                abort_failed = True
            thread.join(timeout)
        timed_out = thread is not None and thread.is_alive()
        with self._lock:
            if self._closed:
                return
            self._fail_active_locked(float(self.wall_clock()))
            for row in self._db.execute("SELECT id FROM clips WHERE state='finalizing'").fetchall():
                self._mark_gone_locked(row["id"], "restart_gone", float(self.wall_clock()))
            self._db.close()
            self._closed = True
        if abort_failed or timed_out:
            raise RuntimeError("internal_error")
