"""The fixed structural query, and the bounds one probe runs inside.

Everything here is a named constant or a field of one frozen dataclass. There is
no settings framework, no configuration file, no environment lookup, no profile,
and no per-request override: a deployment either runs this policy or does not
probe media at all, and a client cannot reach any of it. Nothing in an HTTP
request, a ``CaptureEnvelope``, a ``CaptureIntent``, a filename, a ``file_ref``,
a digest, or a declared MIME type is consulted by any value below, and nothing
below is derived from one.

**That is the whole of the argv-safety argument.** The command line this package
builds is assembled from these constants and from the executable name the
deployment configured, and from nothing else. There is no place in it for a
submitted string to land — not as an argument, not as an option value, and above
all not as a path, because the bytes reach the child on an inherited file
descriptor rather than by being named.

**The structural query is the smallest one that can answer the port.**
:class:`~core.processing.media_probe.MediaProbeResult` has four fields; the
entries requested below are exactly what constructing them needs and nothing
more. Tags, chapters, packets, frames, bit rates, languages, dispositions and
titles are all absent — not filtered out after the fact, but never asked for, so
this adapter cannot accidentally carry them into canonical content.

**The numbers are initial product limits, not measurements.** They bound the
work one submitted file can cause on a laptop; none is claimed to be optimal,
and they are named and easy to find precisely so that changing one is a
deliberate act with a reason attached.
"""

from dataclasses import dataclass
from typing import Final

#: The executable looked up on ``PATH``. A name, not a path: which ffprobe runs
#: is the machine's business, and this package neither installs, downloads, nor
#: vendors one.
FFPROBE_EXECUTABLE: Final = "ffprobe"

#: The only input protocol this adapter uses, and the only one ffprobe is
#: permitted to open.
#:
#: ``fd`` reads from a file descriptor the child already inherited. That is the
#: whole reason it was chosen over ``file``: there is no name involved, so no
#: caller-supplied string can become one, and the bytes ffprobe reads are exactly
#: the bytes this process staged.
INPUT_PROTOCOL: Final = "fd"

#: The input URL handed to ``-i``. ``fd:`` with no number means the descriptor
#: the child was given as its standard input.
INPUT_URL: Final = f"{INPUT_PROTOCOL}:"

#: The protocol whitelist ffprobe runs under: this one and nothing else.
#:
#: Load-bearing rather than belt-and-braces. Some containers can name an external
#: resource — a concat list, an HLS playlist, a referenced segment — and without
#: a whitelist an arbitrary uploaded file could make the probe open an HTTP URL
#: or a path on this machine. With it, every such attempt is refused by ffprobe
#: itself before anything is opened, and the probe fails as an execution failure
#: rather than reaching out.
PROTOCOL_WHITELIST: Final = INPUT_PROTOCOL

#: ffprobe's log level. Errors only, and nothing on standard output.
#:
#: The adapter never reads standard error, so this is not about what it parses.
#: It is about not making ffprobe write prose nobody will read.
LOG_LEVEL: Final = "error"

#: The output format. JSON, because the alternative is parsing a bespoke
#: key-value dialect, and a structural fact is worth a real parser.
PRINT_FORMAT: Final = "json"

#: Container-level entries. ``format_name`` and ``duration``, and no third.
#:
#: ``duration`` is read here and **only** here: ADR-021 fixes the container's
#: declared duration as the duration, and inferring one from a stream would be
#: this adapter reaching a conclusion the container did not state.
FORMAT_ENTRIES: Final[tuple[str, ...]] = ("format_name", "duration")

#: Per-stream entries, in the order they are requested.
#:
#: ``avg_frame_rate`` rather than ``r_frame_rate``: ADR-023 records the choice.
#: The average is the rate the container declares for the stream as a whole,
#: while ``r_frame_rate`` is ffprobe's own guess at the lowest rate that could
#: represent every timestamp exactly — a derived number, not a declaration.
STREAM_ENTRIES: Final[tuple[str, ...]] = (
    "index",
    "codec_type",
    "codec_name",
    "sample_rate",
    "channels",
    "width",
    "height",
    "avg_frame_rate",
)

#: The single ``-show_entries`` argument, built from the two tuples above.
SHOW_ENTRIES: Final = f"format={','.join(FORMAT_ENTRIES)}:stream={','.join(STREAM_ENTRIES)}"

#: Strings ffprobe writes in place of a value it does not have.
#:
#: They are the engine's way of writing ``None`` and are normalized to omission
#: here, at the adapter boundary, so that nothing downstream ever sees one.
#: ``core`` keeps its own private copy of a list like this as a *validator's*
#: vocabulary — a last line of defence against an adapter that failed to do this
#: — and the two are deliberately independent rather than shared.
PLACEHOLDER_VALUES: Final[frozenset[str]] = frozenset({"n/a", "na", "unknown", "none", "null"})

#: ffprobe's spelling of "this stream has no average frame rate".
#:
#: It is not a rate of zero and must never be recorded as one. It arrives as
#: ``None``.
UNKNOWN_FRAME_RATE: Final = "0/0"

#: ``codec_type`` values this adapter describes. Everything else — subtitle,
#: data, attachment, and whatever a future container adds — is ignored, because
#: :class:`~core.processing.media_probe.MediaProbeResult` has nowhere to put it
#: and inventing somewhere would be vocabulary ahead of a decision.
AUDIO_CODEC_TYPE: Final = "audio"
VIDEO_CODEC_TYPE: Final = "video"


@dataclass(frozen=True, slots=True)
class MediaProbeLimits:
    """The bounds one ffprobe invocation runs inside.

    **These are limits, not a sandbox, and the difference is worth being exact
    about**, because three numbers can look like isolation.

    They bound: how long one child process may live; how many bytes of its
    standard output this process will read; and how much of the submitted file is
    held in memory at once while it is staged. A child that outlives its deadline
    is killed and reaped, output past the budget is a refusal rather than a
    truncation, and the staging copy is chunked so a four-gigabyte upload costs
    one buffer rather than four gigabytes of resident memory.

    They do **not** impose a resident-set-size limit on the child, an OS-level
    memory cgroup or ``rlimit``, a CPU quota beyond the wall clock, a filesystem
    namespace, a seccomp filter, or a user change. They do not bound the
    temporary *disk* the staged copy occupies — that is the size of the submitted
    file, which this build already accepted and stored. They do not guarantee
    that ffprobe cannot be killed by the operating system under memory pressure,
    and they do not guarantee that pathological but under-limit input cannot
    cause large transient allocations inside the child. Nothing here puts a
    deadline on the enclosing HTTP request, which has none.

    What they do buy is real, and it is the difference between a bounded adapter
    and an open-ended one: a wedged ffprobe is killed instead of waited on
    forever, and a build that decided to print a hundred megabytes of JSON is
    refused instead of read into this process.
    """

    #: How long one ffprobe invocation may run, on a wall clock.
    #:
    #: Thirty seconds is generous for reading container headers and finite so a
    #: wedged child cannot hold a request open forever. It matches the per-page
    #: and per-image bounds the recognition adapters use, which is a consistency
    #: choice rather than a measurement.
    timeout_seconds: float = 30.0

    #: The most standard output this adapter will read back, in bytes.
    #:
    #: One mebibyte is enormous for the structural query
    #: :data:`SHOW_ENTRIES` describes — a hundred-stream container is a few tens
    #: of kilobytes — so crossing it means the engine answered a different
    #: question than the one it was asked, and its answer is not trusted.
    #: Output past the budget is **not** truncated and parsed: a truncated JSON
    #: document either fails to parse or, worse, parses into a smaller truth.
    max_output_bytes: int = 1024 * 1024

    #: How much of the submitted stream is copied at a time while staging it.
    #:
    #: Only a chunk size, never a bound on the input: a media file is as large as
    #: it is, and this adapter refuses none of it. It exists so that the copy
    #: costs one buffer rather than one file, which is why the staging loop reads
    #: in fixed-size pieces instead of calling ``stream.read()`` with no argument.
    copy_chunk_size: int = 1024 * 1024


#: The limits every deployment gets unless it says otherwise in its own code.
DEFAULT_LIMITS: Final = MediaProbeLimits()

#: How long a startup prerequisite probe may take.
#:
#: Generous for a version banner or a protocol listing, and finite so a broken
#: executable cannot hang startup forever.
PREREQUISITE_TIMEOUT_SECONDS: Final = 30.0
