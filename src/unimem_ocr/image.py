"""The local still-image recognizer: original bytes straight to Tesseract.

The one concrete implementation of
:class:`~core.processing.image_recognition.ImageOcr`. It lives outside ``core``
because that is what makes the canonical policy in
:mod:`core.processing.image_ocr` testable on a machine with no engine installed.

**It decodes nothing.** The submitted PNG or JPEG goes to the engine exactly as
it was staged — no decode, no re-encode, no resize, no colour conversion, no
normalization, and no temporary file. Tesseract links Leptonica, which reads both
formats natively, so the recognizer sees precisely the bytes the submitter
uploaded and no artifact of ours is introduced into the only thing it ever looks
at.

**That is the safer choice, not merely the simpler one.** Decoding in-process
would put a decompression bomb's allocation inside the server, where — as
:class:`~unimem_ocr.policy.ImageOcrLimits` says plainly — the limits do not bound
this process's memory or CPU. Letting the engine decode puts that allocation in a
child that is killed when the clock runs out and whose memory the operating
system reclaims when it exits.

**It imports no imaging library and no rasterizer.** Not ``Pillow``, not
``pypdfium2``, and — deliberately — not :mod:`unimem_ocr.tesseract`, which
imports PDFium at module scope and would drag a renderer into a deployment that
asked only for image OCR. The subprocess work is shared through
:mod:`unimem_ocr.engine`, which is stdlib-only. Enabling ``--image-ocr``
therefore needs no optional Python extra at all: a system Tesseract with the
``eng`` and ``rus`` data files is the whole prerequisite.

**Everything is local.** No network call, no cloud OCR service, no model
download, no telemetry. The engine is invoked with a fixed argument list and
``shell=False``, and no filename, URL, capture title, ``file_ref``, digest, or
request field appears in ``argv``, as an option value, or as a path — the
uploaded filename is never read anywhere in this system, so it cannot reach one.
There is no temporary file, so there is no temporary path to collide, leak, or be
guessed. The image travels in on the child's stdin and the recognized text comes
back on its stdout.
"""

from collections.abc import Mapping
from typing import BinaryIO, Final

from pydantic import JsonValue

from core.processing.image_recognition import (
    ENCODED_BYTE_LIMIT,
    ENCODED_PIXEL_LIMIT,
    ImageOcrExecutionError,
    ImageOcrLimitExceeded,
    ImageOcrResult,
)
from unimem_ocr.engine import (
    LAUNCH_ERROR,
    NONZERO_EXIT,
    TIMEOUT,
    EngineInvocation,
    EngineInvocationError,
    run_engine,
)
from unimem_ocr.policy import (
    DEFAULT_IMAGE_LIMITS,
    IMAGE_DPI_SUPPLIED,
    IMAGE_ENGINE_INPUT,
    IMAGE_READ_CHUNK_SIZE,
    RECOGNITION_LANGUAGES,
    TESSERACT_EXECUTABLE,
    TESSERACT_LANGUAGE_ARGUMENT,
    TESSERACT_OEM,
    TESSERACT_PSM,
    ImageOcrLimits,
)

#: What this adapter calls itself on a content object.
#:
#: The same engine the PDF path names, because it is the same engine. There is
#: deliberately no companion ``rasterizer`` name: nothing rasterizes here, and
#: carrying ``"pypdfium2"`` across because the other adapter reports one would be
#: a fabricated fact in the shape of an observation.
ENGINE_NAME: Final = "tesseract"


class TesseractImageOcr:
    """Recognizes a staged PNG or JPEG with a local Tesseract, decoding nothing."""

    def __init__(
        self,
        *,
        engine_version: str,
        executable: str = TESSERACT_EXECUTABLE,
        limits: ImageOcrLimits = DEFAULT_IMAGE_LIMITS,
    ) -> None:
        """Take the engine's reported version, where to find it, and its bounds.

        ``engine_version`` is required rather than defaulted because it is
        recorded on every content object this adapter contributes to, and a
        placeholder would travel into durable canonical content and stay there.
        :func:`unimem_ocr.build_tesseract_image_ocr` obtains it by asking the
        installed executable during startup validation, which is the only honest
        source.

        ``executable`` and ``limits`` are deployment configuration: they are
        supplied here, at composition time, and nothing reads them from an
        environment variable, a file, or a request.
        """
        self._engine_version = engine_version
        self._executable = executable
        self._limits = limits

    def recognize_image(
        self,
        stream: BinaryIO,
        *,
        mime_type: str,
        encoded_width: int,
        encoded_height: int,
    ) -> ImageOcrResult:
        """Recognize one image, or refuse it before reading a byte of it.

        The sequence, and where each bound sits in it::

            check the encoded pixel count      -- before the stream is read
            read the original, bounded         -- before the child exists
            run the engine                     -- one invocation, one timeout
            return exactly what it printed

        Both refusals happen before a subprocess exists, and the pixel refusal
        happens before the stream is touched at all: the caller has opened a
        handle to satisfy this signature, and an over-budget image costs exactly
        that handle, closed unread.

        ``mime_type`` is recorded and not re-derived. The caller has already
        verified the leading bytes against it, and this adapter does not sniff,
        re-check, or reroute on it — the engine reads whichever of the two formats
        arrived.

        Raises :class:`~core.processing.image_recognition.ImageOcrLimitExceeded`
        for either documented bound, carrying a structured reason so the caller
        never reads a message. Raises
        :class:`~core.processing.image_recognition.ImageOcrExecutionError` when no
        trusted result came back.
        """
        self._check_pixel_bound(encoded_width, encoded_height)
        image = self._read_bounded(stream)
        text = self._recognize(image)
        return ImageOcrResult(
            text=text,
            engine=ENGINE_NAME,
            engine_version=self._engine_version,
            settings=self.settings(),
        )

    def settings(self) -> Mapping[str, JsonValue]:
        """The effective recognition configuration, as recorded on the content.

        Every value that shaped what the engine saw and how it was asked to read
        it, plus the limits that were in force. It is a description of this run,
        not a schema: it lands in ``ContentObject.metadata``, which is free-form
        JSON, so adding a setting later does not change any contract.

        Two entries exist to record an *absence* precisely, because an absence is
        the thing a later reader is most likely to guess wrong about.
        ``dpi_supplied`` says this invocation named no resolution — so whatever
        resolution the engine assumed is the engine's own behaviour and not a
        claim this system made about the picture. ``input`` says the engine was
        handed the original encoded bytes, so no decode, re-encode, or resize of
        ours sits between the submitted file and what was read.

        There is no confidence score: the engine is not asked for one, and an
        invented number would be read as a measurement. There is no detected
        language, no orientation, and no rasterizer.
        """
        return {
            "languages": TESSERACT_LANGUAGE_ARGUMENT,
            "language_order": list(RECOGNITION_LANGUAGES),
            "oem": TESSERACT_OEM,
            "psm": TESSERACT_PSM,
            "dpi_supplied": IMAGE_DPI_SUPPLIED,
            "input": IMAGE_ENGINE_INPUT,
            "orientation_detection": False,
            "deskew": False,
            "retries": 0,
            "max_encoded_pixels": self._limits.max_encoded_pixels,
            "max_encoded_bytes": self._limits.max_encoded_bytes,
            "recognition_timeout_seconds": self._limits.recognition_timeout_seconds,
        }

    def _check_pixel_bound(self, width: int, height: int) -> None:
        """Refuse an image whose declared pixel count is over budget, before reading it.

        The multiplication is on integers the caller read out of an already
        validated header, so this costs nothing and — crucially — happens before
        any decode, any allocation of pixels, and any child process. That ordering
        is what ADR-019 requires of the first path in this system that causes a
        decoder to look at pixels.

        Refusal, not downscaling. An image recognized at a silently reduced size
        would produce text whose quality nothing in the result explains.
        """
        pixels = width * height
        if pixels > self._limits.max_encoded_pixels:
            raise ImageOcrLimitExceeded(
                f"the image encodes {pixels} pixels, over this build's image recognition "
                f"limit of {self._limits.max_encoded_pixels}",
                reason=ENCODED_PIXEL_LIMIT,
                limit=self._limits.max_encoded_pixels,
            )

    def _read_bounded(self, stream: BinaryIO) -> bytes:
        """Read the original forward, refusing once it grows past the byte bound.

        Forward only: nothing seeks, nothing asks the stream how long it is — the
        raw store's reference carries no size and the port exposes none — so this
        works against a non-seekable backend without a second code path.

        **The last request is narrowed deliberately.** Asking for a full chunk
        when only a few bytes of headroom remain would pull up to a whole chunk
        past the limit into memory before noticing, which would make the bound a
        suggestion. Instead each request is at most ``remaining + 1``: the ``+ 1``
        is a sentinel that makes "exactly at the limit" and "one byte over"
        distinguishable, and it is the entire overshoot. An original of exactly
        ``max_encoded_bytes`` is accepted; one byte more is refused.

        A refusal here happens **before any subprocess exists**, and the bytes
        read so far are dropped as this unwinds.
        """
        limit = self._limits.max_encoded_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            # One past the bound is all the headroom the reader ever takes, so
            # crossing it is detectable while the memory held stays within the
            # configured limit plus that single sentinel byte.
            want = min(IMAGE_READ_CHUNK_SIZE, limit - total + 1)
            chunk = stream.read(want)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > limit:
                raise ImageOcrLimitExceeded(
                    f"the image is larger than the {limit} encoded bytes this build will "
                    f"hold for one recognition",
                    reason=ENCODED_BYTE_LIMIT,
                    limit=limit,
                )
            chunks.append(chunk)

    def _recognize(self, image: bytes) -> str:
        """Run the engine over the original bytes and return exactly what it printed.

        The policy is fixed and identical in every deployment: ``eng+rus`` in that
        order, OEM 1, PSM 3, one invocation, no retry, no orientation detection,
        no deskew — and **no ``--dpi``**, because this build rasterized nothing
        and read no physical density, so it has no resolution to report. See
        :data:`~unimem_ocr.policy.IMAGE_DPI_SUPPLIED`.

        PSM 3 is Tesseract's own default, tuned for a document page. A photograph
        of a sign or a screenshot with scattered labels is not a page, and
        recognition on such material may well be worse than a sparse-text mode
        would manage. That is a recorded limitation rather than a knob: choosing a
        mode per image would be a guess that changes what a picture is remembered
        as saying.

        Every invocation failure becomes an
        :class:`~core.processing.image_recognition.ImageOcrExecutionError`, and the
        translation is deliberately lossy in one direction: a nonzero exit is
        **never** turned into a verdict about the image. Tesseract reports a dead
        or unhappy child the same way whether the engine is broken, the machine is
        out of memory, or the encoded data behind the header this build validated
        is something Leptonica cannot decode. The only thing that might separate
        those is the child's standard error, which is captured, dropped
        unexamined, and never parsed — so what this adapter reports is that no
        trusted result came back, which is all it actually knows.
        """
        invocation = EngineInvocation(
            executable=self._executable,
            languages=TESSERACT_LANGUAGE_ARGUMENT,
            oem=TESSERACT_OEM,
            psm=TESSERACT_PSM,
            timeout=self._limits.recognition_timeout_seconds,
            dpi=None,
        )
        try:
            return run_engine(image, invocation)
        except EngineInvocationError as exc:
            raise ImageOcrExecutionError(self._failure_message(exc)) from (exc.__cause__ or exc)

    def _failure_message(self, failure: EngineInvocationError) -> str:
        """This path's own sentence for a structured invocation failure.

        Branching on :attr:`~unimem_ocr.engine.EngineInvocationError.reason` and
        never on the runner's message text: a message is not an interface. The
        wording says nothing about pages, because there are none, and nothing
        about what the failure proves, because it proves nothing beyond itself.

        Nothing here reaches a client. The delivery layer answers every one of
        these with one fixed public sentence; these are for a server log.
        """
        if failure.reason == TIMEOUT:
            return (
                f"the OCR engine did not finish this image within "
                f"{self._limits.recognition_timeout_seconds:.1f}s and was killed"
            )
        if failure.reason == LAUNCH_ERROR:
            return (
                f"the OCR engine {self._executable!r} could not be run while recognizing "
                f"an image ({failure.detail})"
            )
        if failure.reason == NONZERO_EXIT:
            return (
                f"the OCR engine exited with status {failure.returncode} on this image; "
                f"this build cannot tell an engine or machine failure from encoded image "
                f"data the engine could not decode, and claims neither"
            )
        return "the OCR engine's output for this image was not valid UTF-8"
