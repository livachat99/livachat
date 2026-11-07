from __future__ import annotations

import ctypes
import ctypes.util

# ---------------------------------------------------------------------------
# Native library loading — robust across install layouts
# ---------------------------------------------------------------------------

_OPUS_LIB: ctypes.CDLL | None = None


def _load_opus() -> ctypes.CDLL:
    global _OPUS_LIB
    if _OPUS_LIB is not None:
        return _OPUS_LIB

    candidates = [
        "/usr/local/homebrew/opt/opus/lib/libopus.dylib",
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/lib/libopus.so.0",
        "/usr/local/lib/libopus.so",
    ]
    path = ctypes.util.find_library("opus")
    if path:
        candidates.insert(0, path)

    for loc in candidates:
        try:
            lib = ctypes.CDLL(loc)
            _OPUS_LIB = lib
            return lib
        except OSError:
            continue

    raise OSError("Cannot find libopus. Install it via 'brew install opus' or set DYLD_LIBRARY_PATH.")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPUS_APPLICATION_VOIP = 2048
OPUS_SET_VBR_REQUEST = 4006
OPUS_SET_COMPLEXITY_REQUEST = 4010
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_SIGNAL_REQUEST = 4024
OPUS_SIGNAL_VOICE = 3001
OPUS_RESET_STATE = 4028

_FRAME_SAMPLES: dict[int, int] = {
    20: 320,  # 16000 * 0.020 = 320 samples  (but depends on sample rate)
    40: 640,  # 16000 * 0.040 = 640
}

_SAMPLES_PER_MS: dict[int, float] = {
    16000: 16.0,
    24000: 24.0,
}


def _frame_samples(frame_ms: int, sample_rate: int) -> int:
    return int(sample_rate * frame_ms / 1000)


def _max_data_bytes(frame_samples: int) -> int:
    """Worst-case Opus packet size for a given frame (generous upper bound)."""
    return max(frame_samples * 2, 4000)


class _OpusState(ctypes.Structure):
    pass


_OpusStatePtr = ctypes.POINTER(_OpusState)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class OpusEncoder:
    """Thin ctypes wrapper around libopus encoder.

    Parameters match the Android SDK (§4.8):
    * application = VOIP
    * bitrate = 32000 CBR
    * complexity = 5
    * mono
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        bitrate: int = 32000,
        complexity: int = 5,
        channels: int = 1,
    ) -> None:
        if channels != 1:
            # encode() sizes its PCM buffer as mono; a multi-channel encoder
            # would read past it (OOB heap read) and put heap data on the wire.
            raise ValueError(f"OpusEncoder supports mono only (channels=1), got {channels}")
        self._lib = _load_opus()
        self._channels = channels
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._frame_size = _frame_samples(frame_ms, sample_rate)

        # -- configure function signatures --------------------------------
        self._lib.opus_encoder_get_size.argtypes = [ctypes.c_int]
        self._lib.opus_encoder_get_size.restype = ctypes.c_int

        self._lib.opus_encoder_create.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_encoder_create.restype = _OpusStatePtr

        self._lib.opus_encode.argtypes = [
            _OpusStatePtr,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int32,
        ]
        self._lib.opus_encode.restype = ctypes.c_int32

        # opus_encoder_ctl is variadic: declare ONLY the fixed args. On Apple
        # arm64 the callee reads varargs from the stack; declaring the third
        # arg passes it in a register and the ctl reads garbage (e.g. VBR
        # returns OPUS_BAD_ARG -1). Extra args are passed positionally below.
        self._lib.opus_encoder_ctl.argtypes = [_OpusStatePtr, ctypes.c_int]
        self._lib.opus_encoder_ctl.restype = ctypes.c_int

        self._lib.opus_encoder_destroy.argtypes = [_OpusStatePtr]
        self._lib.opus_encoder_destroy.restype = None

        # -- create state -------------------------------------------------
        err = ctypes.c_int(0)
        self._state = self._lib.opus_encoder_create(
            ctypes.c_int(sample_rate),
            ctypes.c_int(channels),
            ctypes.c_int(OPUS_APPLICATION_VOIP),
            ctypes.byref(err),
        )
        if err.value != 0 or not self._state:
            raise OSError(f"opus_encoder_create failed: {err.value}")

        # -- apply settings -----------------------------------------------
        self._ctl(OPUS_SET_VBR_REQUEST, 0)  # CBR
        self._ctl(OPUS_SET_BITRATE_REQUEST, bitrate)
        self._ctl(OPUS_SET_COMPLEXITY_REQUEST, complexity)
        self._ctl(OPUS_SET_SIGNAL_REQUEST, OPUS_SIGNAL_VOICE)

    # -- public API -------------------------------------------------------

    def encode(self, pcm: bytes) -> bytes:
        """Encode one frame of PCM16 mono to an Opus packet.

        *pcm* must be exactly ``frame_samples * 2`` bytes long.
        Returns the encoded Opus packet as bytes.
        """
        assert len(pcm) == self._frame_size * 2, f"PCM must be {self._frame_size * 2} bytes, got {len(pcm)}"

        pcm_buf = (ctypes.c_int16 * self._frame_size).from_buffer_copy(pcm)
        max_bytes = _max_data_bytes(self._frame_size)
        opus_buf = ctypes.create_string_buffer(max_bytes)

        nb = self._lib.opus_encode(
            self._state,
            pcm_buf,
            ctypes.c_int(self._frame_size),
            opus_buf,
            ctypes.c_int32(max_bytes),
        )
        if nb < 0:
            raise OSError(f"opus_encode failed: {nb}")

        return ctypes.string_at(ctypes.addressof(opus_buf), nb)

    @property
    def frame_size(self) -> int:
        return self._frame_size

    def reset(self) -> None:
        self._ctl(OPUS_RESET_STATE, 0)

    def close(self) -> None:
        if self._state:
            self._lib.opus_encoder_destroy(self._state)
            self._state = None

    # -- internal ---------------------------------------------------------

    def _ctl(self, request: int, value: int) -> None:
        rc = self._lib.opus_encoder_ctl(self._state, request, ctypes.c_int(value))
        if rc < 0:
            raise OSError(f"opus_encoder_ctl({request}) failed: {rc}")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------


class OpusDecoder:
    """Thin ctypes wrapper around libopus decoder."""

    def __init__(self, sample_rate: int = 16000, frame_ms: int = 20, channels: int = 1) -> None:
        if channels != 1:
            # decode()/decode_missing() size output buffers as mono; a stereo
            # decoder writes past them (OOB heap write) and the returned byte
            # count would truncate to half a frame.
            raise ValueError(f"OpusDecoder supports mono only (channels=1), got {channels}")
        self._lib = _load_opus()
        self._channels = channels
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._frame_size = _frame_samples(frame_ms, sample_rate)

        # -- configure function signatures --------------------------------
        self._lib.opus_decoder_get_size.argtypes = [ctypes.c_int]
        self._lib.opus_decoder_get_size.restype = ctypes.c_int

        self._lib.opus_decoder_create.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_decoder_create.restype = _OpusStatePtr

        self._lib.opus_decode.argtypes = [
            _OpusStatePtr,
            ctypes.c_char_p,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.opus_decode.restype = ctypes.c_int32

        # Variadic — see the encoder note above.
        self._lib.opus_decoder_ctl.argtypes = [_OpusStatePtr, ctypes.c_int]
        self._lib.opus_decoder_ctl.restype = ctypes.c_int

        self._lib.opus_decoder_destroy.argtypes = [_OpusStatePtr]
        self._lib.opus_decoder_destroy.restype = None

        # -- create state -------------------------------------------------
        err = ctypes.c_int(0)
        self._state = self._lib.opus_decoder_create(
            ctypes.c_int(sample_rate),
            ctypes.c_int(channels),
            ctypes.byref(err),
        )
        if err.value != 0 or not self._state:
            raise OSError(f"opus_decoder_create failed: {err.value}")

    # -- public API -------------------------------------------------------

    def decode(self, opus_packet: bytes, *, fec: bool = False) -> bytes:
        """Decode one Opus packet to PCM16 mono.

        Returns PCM16 bytes.  If *fec* is True and the packet is flagged as
        lost, attempt Forward Error Correction.
        """
        max_samples = self._frame_size * 2  # generous: up to 2× frame size
        pcm_buf = (ctypes.c_int16 * max_samples)()

        nb = self._lib.opus_decode(
            self._state,
            ctypes.c_char_p(opus_packet),
            ctypes.c_int32(len(opus_packet)),
            pcm_buf,
            ctypes.c_int(max_samples),
            ctypes.c_int(1 if fec else 0),
        )
        if nb < 0:
            raise OSError(f"opus_decode failed: {nb}")

        return ctypes.string_at(ctypes.addressof(pcm_buf), nb * ctypes.sizeof(ctypes.c_int16))

    def decode_missing(self, frame_size: int | None = None) -> bytes:
        """Generate comfort noise / PLC for a missing frame."""
        fs = frame_size or self._frame_size
        max_samples = fs
        pcm_buf = (ctypes.c_int16 * max_samples)()

        nb = self._lib.opus_decode(
            self._state,
            ctypes.c_char_p(None),
            ctypes.c_int32(0),
            pcm_buf,
            ctypes.c_int(max_samples),
            ctypes.c_int(0),
        )
        if nb < 0:
            raise OSError(f"opus_decode (plc) failed: {nb}")
        return ctypes.string_at(ctypes.addressof(pcm_buf), nb * ctypes.sizeof(ctypes.c_int16))

    @property
    def frame_size(self) -> int:
        return self._frame_size

    def reset(self) -> None:
        self._ctl(OPUS_RESET_STATE, 0)

    def close(self) -> None:
        if self._state:
            self._lib.opus_decoder_destroy(self._state)
            self._state = None

    # -- internal ---------------------------------------------------------

    def _ctl(self, request: int, value: int) -> None:
        rc = self._lib.opus_decoder_ctl(self._state, request, ctypes.c_int(value))
        if rc < 0:
            raise OSError(f"opus_decoder_ctl({request}) failed: {rc}")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
