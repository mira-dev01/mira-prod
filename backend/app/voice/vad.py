"""Pre-warmed Silero VAD so a call doesn't pay ONNX model load cost.

SileroVADAnalyzer.__init__ synchronously builds an onnxruntime.InferenceSession
from the packaged .onnx file -- ~2s, confirmed via call logs, paid again on
every single call since app/voice/pipeline.py builds a fresh analyzer per call
(VAD state -- _vad_buffer/_vad_state/the model's _state/_context arrays -- is
per-stream and can't be shared across concurrent calls). The InferenceSession
itself has no per-call state and is safe to reuse concurrently, so it's built
once, here, at import time, and each call gets its own lightweight analyzer
wrapping that shared session instead of loading its own.
"""

import logging

from pipecat.audio.vad import silero as silero_module
from pipecat.audio.vad.silero import SileroOnnxModel, SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

logger = logging.getLogger(__name__)


def _load_shared_model() -> SileroOnnxModel:
    try:
        import importlib_resources as impresources

        model_file_path = str(impresources.files("pipecat.audio.vad.data").joinpath("silero_vad.onnx"))
    except BaseException:
        from importlib import resources as impresources

        with impresources.path("pipecat.audio.vad.data", "silero_vad.onnx") as f:
            model_file_path = str(f)

    return SileroOnnxModel(model_file_path, force_onnx_cpu=True)


logger.info("Pre-loading Silero VAD ONNX model at process startup...")
_shared_model = _load_shared_model()
logger.info("Silero VAD ONNX model pre-loaded")


def create_vad_analyzer(params: VADParams) -> SileroVADAnalyzer:
    """A SileroVADAnalyzer for one call, reusing the pre-warmed ONNX session.

    Runs the real SileroVADAnalyzer.__init__ so every per-instance attribute
    the installed pipecat version expects exists (a hand-rolled __new__ copy
    of __init__ broke when a newer pipecat added `_windows_since_reset`:
    every audio frame raised AttributeError, VAD never fired, and the
    silence watchdog hung up on the guest). Only the model construction is
    swapped, for a fresh SileroOnnxModel wrapping the shared session.
    """

    def _shared_session_model(*_args, **_kwargs) -> SileroOnnxModel:
        model = SileroOnnxModel.__new__(SileroOnnxModel)
        model.session = _shared_model.session
        model.reset_states()
        model.sample_rates = [8000, 16000]
        return model

    original = silero_module.SileroOnnxModel
    silero_module.SileroOnnxModel = _shared_session_model
    try:
        return SileroVADAnalyzer(sample_rate=None, params=params)
    finally:
        silero_module.SileroOnnxModel = original
