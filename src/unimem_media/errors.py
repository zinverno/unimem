"""The one failure this package raises before it is allowed to serve anything."""


class MediaPrerequisiteError(Exception):
    """Local media probing was asked for and this machine cannot provide it.

    Raised only while the application is being assembled, never during a
    capture. It means one of the two things a media-enabled deployment needs is
    absent: an ``ffprobe`` executable this process can launch, or — in the build
    that is installed — the input protocol this adapter feeds its bytes through.

    It exists as its own type, separate from
    :class:`~core.processing.media_probe.MediaProbeExecutionError`, because the
    two answer different questions. This one says "do not start"; that one says
    "a running server could not obtain a trustworthy structural result for one
    capture". Conflating them would make a misconfigured deployment look like a
    transient failure, and a server that starts and then answers 503 to every
    audio and video capture is strictly worse than one that refuses to start and
    says why.

    It deliberately lives here and not in ``core``. Which engine reads a
    container, and whether that engine is installed, is a fact about a concrete
    adapter's deployment; ``core`` neither knows the engine's name nor has
    anything to do with the answer. :mod:`core.processing.media_probe` says so in
    as many words.

    Its message names what is missing and, where there is one, what would provide
    it. It is read by whoever typed the command, printed to a terminal, and never
    returned over HTTP.
    """
