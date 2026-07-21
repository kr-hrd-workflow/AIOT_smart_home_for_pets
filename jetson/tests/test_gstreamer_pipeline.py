import unittest
import os
import tempfile
import threading

from jetson.tensorrt_yolo import GstreamerEncoder, build_gstreamer_pipeline, validate_mp4_description


class GstreamerPipelineTests(unittest.TestCase):
    def test_discoverer_output_must_match_actual_media_contract(self):
        description = (
            "video: video/x-h264, width=(int)640, height=(int)480, framerate=(fraction)10/1, "
            "chroma-format=(string)4:2:0, bit-depth-luma=(uint)8, bit-depth-chroma=(uint)8\n"
            "container format: Quicktime\nDuration: 0:00:03.000000000\n"
        )
        validate_mp4_description(description, 30)
        with self.assertRaisesRegex(RuntimeError, "invalid_media"):
            validate_mp4_description(description.replace("width=(int)640", "width=(int)320"), 30)
        with self.assertRaisesRegex(RuntimeError, "invalid_media"):
            validate_mp4_description(description.replace("0:00:03.000000000", "0:00:04.000000000"), 30)

    def test_pipeline_is_the_frozen_hardware_path(self):
        pipeline = build_gstreamer_pipeline('/tmp/petcare clip "proof".partial.mp4')
        self.assertEqual(
            pipeline,
            'appsrc name=source is-live=false format=time '
            'caps=video/x-raw,format=BGR,width=640,height=480,framerate=10/1 '
            '! videoconvert ! video/x-raw,format=BGRx ! nvvidconv '
            '! video/x-raw(memory:NVMM),format=NV12,width=640,height=480,framerate=10/1 '
            '! nvv4l2h264enc insert-sps-pps=true ! h264parse ! qtmux '
            '! filesink location="/tmp/petcare clip \\"proof\\".partial.mp4"',
        )
        self.assertNotIn("x264enc", pipeline)
        self.assertNotIn("omxh264enc", pipeline)

    def test_abort_is_idempotent_and_forces_active_pipeline_to_null(self):
        class State(object):
            NULL = "null"

        class Gst(object):
            pass

        Gst.State = State

        class Pipeline(object):
            def __init__(self):
                self.states = []

            def set_state(self, state):
                self.states.append(state)

        pipeline = Pipeline()
        encoder = GstreamerEncoder.__new__(GstreamerEncoder)
        encoder.Gst = Gst
        encoder._lock = threading.Lock()
        encoder._pipeline = pipeline
        encoder._aborting = None
        encoder._abort_requested = False

        encoder.abort()
        encoder.abort()

        self.assertEqual(pipeline.states, ["null"])

    def test_abort_before_call_is_terminal(self):
        class State(object):
            NULL = "null"

        class Gst(object):
            pass

        Gst.State = State
        encoder = GstreamerEncoder.__new__(GstreamerEncoder)
        encoder.Gst = Gst
        encoder._lock = threading.Lock()
        encoder._pipeline = None
        encoder._aborting = None
        encoder._abort_requested = False

        encoder.abort()

        with self.assertRaisesRegex(RuntimeError, "encoder_aborted"):
            encoder((), None)

    def test_abort_during_pipeline_creation_never_starts_playback(self):
        entered = threading.Event()
        release = threading.Event()

        class State(object):
            NULL = "null"
            PLAYING = "playing"

        class StateChangeReturn(object):
            FAILURE = "failure"

        class Pipeline(object):
            def __init__(self):
                self.states = []

            def get_by_name(self, unused_name):
                return object()

            def get_bus(self):
                return object()

            def set_state(self, state):
                self.states.append(state)
                return "ok"

        pipeline = Pipeline()

        class Gst(object):
            @staticmethod
            def parse_launch(unused_description):
                entered.set()
                release.wait(2)
                return pipeline

        Gst.State = State
        Gst.StateChangeReturn = StateChangeReturn

        encoder = GstreamerEncoder.__new__(GstreamerEncoder)
        encoder.Gst = Gst
        encoder._lock = threading.Lock()
        encoder._pipeline = None
        encoder._aborting = None
        encoder._abort_requested = False
        errors = []
        with tempfile.TemporaryDirectory() as directory:
            partial = os.path.join(directory, "clip.partial.mp4")
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            worker = threading.Thread(target=lambda: self._capture_error(errors, encoder, partial))
            worker.start()
            self.assertTrue(entered.wait(2))
            encoder.abort()
            release.set()
            worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("playing", pipeline.states)
        self.assertTrue(errors)

    @staticmethod
    def _capture_error(errors, encoder, partial):
        try:
            encoder(((1, b"jpeg"),), partial)
        except BaseException as error:
            errors.append(error)


if __name__ == "__main__":
    unittest.main()
