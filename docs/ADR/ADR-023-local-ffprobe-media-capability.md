# ADR-023: The local ffprobe media capability, and why a bounded adapter is not a sandbox

Status: **accepted (Macro Phase 5A, PR 3); implemented.** This slice supplies the
concrete engine behind the seam
[ADR-021](ADR-021-original-first-time-based-media-ingestion.md) fixed and
[ADR-022](ADR-022-engine-independent-media-processing.md) built the policy for:
an adapter package outside `core`, a probe that runs a system `ffprobe`, a
startup gate, and a `--media` flag. **Neither ADR-021 nor ADR-022 is rewritten**,
and neither is any earlier one. Macro Phase 4 remains
[closed](ADR-020-opt-in-local-image-ocr.md), Macro Phase 3 remains
[closed](ADR-019-still-image-ingestion.md#macro-phase-3-is-closed), Macro Phase 2
remains [closed](ADR-016-pdf-document-ingestion.md#macro-phase-2-is-closed),
Macro Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed), and Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).

No contract change, no enum change, no lifecycle state, no persistence migration,
no route, no replay change, and no browser change: the schema stays `0.3`. **No
new Python dependency of any kind, and deliberately no `[media]` extra** — the
adapter is standard library plus `core`, and its one prerequisite is a program
`pip` cannot install. `src/core/` is byte-for-byte unchanged by this slice.

**Macro Phase 5A remains open after this PR.** See
[Macro Phase 5A is not closed here](#macro-phase-5a-is-not-closed-here).

**What this PR does not contain** is as load-bearing as what it does. There is no
transcription, no interpreted media segment, no keyframe, no thumbnail, no
extracted audio track, no primary-stream selection, no embedded tag, no raw
ffprobe JSON in canonical content, no engine identity in canonical metadata, no
`ffmpeg` invocation anywhere under `src/`, no cloud media service, and no
capability registry. Transcription is Phase 5B and nothing below should be read
as deciding it.

## Context

After Phase 5A-2 the media path was complete except for one thing: nothing could
produce a `MediaProbeResult`. `core.processing.media_probe` declared the port,
`AudioProcessor` and `VideoProcessor` interpreted its results, `CaptureIntake`
gated the modality, and `build_local_app` accepted a `media_probe=` argument —
but the only way to reach any of it was to construct a probe in Python and hand
it in. An operator had no way to run a media deployment at all.

The question this slice answers is therefore narrow and entirely about
execution: **how does this build read a container, given that `core` may not
import a media framework, a codec binding, or `subprocess`?**

The answer's shape was already fixed by the two OCR capabilities: the machinery
lives in an adapter package outside `core`, the policy stays in `core`, and the
delivery layer builds the concrete adapter. What was genuinely undecided was
everything about running `ffprobe` safely — how the bytes reach it, what it is
allowed to open, what it is asked for, what happens when it misbehaves, and what
"bounded" honestly means.

## Decision

### The adapter lives outside `core`, in `unimem_media`

`src/unimem_media/` is a new external adapter package, a sibling of
`unimem_ocr`. It may depend on `core`; `core` may never depend on it, and a test
walks every module under `src/core/` to prove no such import exists.

The reason is the one `core.processing.media_probe` already gives: the canonical
policy that decides what a probe result *means* must stay testable on a machine
with no media tooling installed. That property is what makes `Quality gates` — a
job with no FFmpeg — meaningful evidence rather than a formality, and it would be
lost the moment `core` named an engine.

The package is **standard library plus `core`**, which is stronger than
`unimem_ocr` manages and is the reason there is no `[media]` extra:

- an extra would suggest `pip` could supply the prerequisite, and it cannot —
  `ffprobe` is a system program installed by the machine's package manager;
- there is nothing for an extra to install, because the adapter imports no
  third-party package at all.

The package therefore ships in the wheel unconditionally. Leaving it out would
mean `--media` failed with "no such module" on a machine that had FFmpeg and
everything else it needs.

### The port still takes a `BinaryIO`, and the adapter stages it privately

ADR-021 fixed that `MediaProbe.probe` receives a binary stream and nothing else —
no MIME type, no capture id, no filename, no `RawObjectRef`, and above all no
path, because "a path is a filesystem instruction and this port must not be able
to express one."

`ffprobe` wants a source it can *seek*. Container headers live at both ends of an
MP4, and a demuxer that cannot rewind either fails or reports less than the file
says. A pipe is not seekable. Three ways out were available and only one keeps
the port's promise:

1. **Widen the port to take a path.** Rejected. It would hand every future
   adapter a caller-supplied filesystem instruction and make the raw store's
   layout part of the seam, which is exactly what ADR-021 refused.
2. **Feed ffprobe through a pipe.** Rejected. Some containers genuinely cannot be
   read forward-only, so the capability would work for WAV and MP3 and quietly
   under-report MP4s — a correctness failure that looks like a policy result.
3. **Stage the bytes into a private temporary file inside the adapter.**
   Accepted.

The third is not a loophole. The port's rule is about what the *caller* can
express, and staging is an implementation detail behind it: a caller has no way
to supply, influence, or observe the path, because there is no path. The adapter
copies the stream into a `tempfile.TemporaryFile`, which is unlinked at creation
on every platform this project supports, so the file has no name in the
filesystem to collide with, guess at, race, or leak — and no name to hand to
ffprobe even by mistake.

The copy is **chunked**, in fixed-size pieces, never a bare `stream.read()`. A
media file may be gigabytes, and the whole point of a structural probe is that it
reads headers; holding the payload in this process to hand it to a child that
will read the first and last few kilobytes of it would be absurd. A test observes
the read sizes directly, because the difference is invisible in the result.

The caller's handle is read forward and **not closed**: the port says the caller
owns it.

### ffprobe is pointed at `fd:`, and no request-controlled string reaches argv

The staged file is the child's standard input, and the input is named `fd:` — the
descriptor the child already inherited. That is the whole argv-safety argument,
and it is structural rather than defensive: there is no place in the command line
for a submitted string to land, because the input is not named at all.

No filename, capture id, source URL, raw-store path, `file_ref`, digest, declared
MIME type, capture title, or any other request-controlled value appears in argv —
not as an argument, not as an option value, and not as a path. Every element
after the executable is a constant in `unimem_media.policy`, and the exact vector
is frozen element by element in a unit test:

```
ffprobe -hide_banner -loglevel error -protocol_whitelist fd
        -print_format json -show_entries <fixed query> -i fd:
```

`shell=False` and a *list* of arguments, so no shell string is constructed: there
is nothing to quote and nothing to interpret. A test asserts that no module in
the package contains `shell=True` or `os.system`.

### The protocol whitelist is load-bearing, not belt-and-braces

`-protocol_whitelist fd` permits exactly one protocol and nothing else.

This matters because some containers can name an external resource — a concat
list, an HLS playlist, a referenced segment — and without a whitelist an
arbitrary uploaded file could make the probe open an HTTP URL or a path on this
machine. That is a server-side request forgery and a local file read, reachable
by anyone who can upload. With the whitelist, ffprobe refuses every such attempt
itself, before anything is opened, and the probe fails as an execution failure
rather than reaching out. Verified against the real engine: `file:` and `http:`
inputs are refused with `Protocol 'file' not on whitelist 'fd'!` and a nonzero
exit.

The whitelist is the same constant the input URL is built from, so the two cannot
drift into a state where the adapter names a protocol it did not permit.

### The structural query is the smallest one that answers the port

`MediaProbeResult` has four fields. The query asks for exactly what constructing
them needs:

```
format=format_name,duration
stream=index,codec_type,codec_name,sample_rate,channels,width,height,avg_frame_rate
```

Tags, chapters, packets, frames, bit rates, languages, dispositions, titles and
payload data are **not requested**. That is a stronger statement than filtering
them out afterwards: they cannot arrive, so no future edit to the normalizer can
accidentally carry one into canonical content, and the probe cannot become a read
oracle over metadata somebody embedded in their file.

`duration` is read at format level and **only** there. ADR-021 fixed the
container's declaration as the duration; inferring one from a stream would be
this adapter concluding something the container did not state.

### `avg_frame_rate`, kept as an exact rational

ffprobe offers two frame rates and they are not the same fact.

`r_frame_rate` is ffprobe's own computed guess at the lowest rate that could
represent every timestamp in the stream exactly. `avg_frame_rate` is the average
the container declares for the stream as a whole. ADR-021's `frame_rate` is
documented as what "the container carries", so the declaration is the one that
belongs there; a derived guess recorded as an observation would be the adapter
adding a conclusion of its own.

It is kept as a **rational string**. `30000/1001` is the exact fact; `29.97` is a
lossy rendering of it that no later stage could undo. The one arithmetic applied
is reduction to lowest terms, which is exact and changes no value: `60/2` and
`30/1` are the same rational, and `core`'s validator requires the canonical
spelling so that two probes of one file cannot produce two unequal stored
objects.

`0/0` is ffprobe's spelling of "no average frame rate", and it arrives as `None`.
A zero numerator means the same thing however it is spelled. A zero *denominator*
with a nonzero numerator is not a rational at all, and is a failure.

### Normalization happens at this boundary, and manufactures nothing

ffprobe's representation is engine vocabulary and none of it may reach a stored
content object. The adapter translates:

- **Container** — `format_name` is one comma-separated string. It is split,
  trimmed, lowercased, de-duplicated and sorted into the tuple the port requires.
  The *whole* family is kept, because an ISO base media file genuinely is
  `mov`, `mp4`, `m4a`, `3gp`, `3g2` and `mj2` at once, and picking one would be
  the sniff-and-rewrite ADR-021 forbids. **Missing or unusable container
  information is an execution failure**: a probe that recognized no container
  recognized nothing.
- **Duration** — format level only. A finite non-negative number becomes a
  `float`; absent, or ffprobe's unavailable representation, becomes `None`. A
  live-recorded or truncated file declaring none is a fact rather than a failure.
  Verified against the real engine, which under `-show_entries` omits the key
  entirely rather than writing `"N/A"`; both spellings are handled.
- **Audio streams** — `codec_type == "audio"` only. `index` is required.
  `codec_name`, `sample_rate` and `channels` are recorded when declared.
- **Video streams** — `codec_type == "video"` only, on the same terms, plus
  `width`, `height` and the frame rate above.
- **Everything else is ignored.** Subtitle, data, attachment, and any
  `codec_type` this build does not recognize are simply not described. A gap in
  the index numbering is therefore normal, and a container carrying a subtitle
  track is perfectly ordinary rather than a refusal.
- **Both collections are sorted by the container's own stream index**, because
  the order is part of the value.

Two rules govern the edges, and they pull the same way:

**Nothing is manufactured.** A value the container did not declare is `None` —
never `0`, never `""`, never a guess. Placeholders like `"N/A"` become omission,
because a stored object recording `codec: "n/a"` claims an observation nobody
made.

**Nothing broken is repaired into an omission.** A value that is *present* and
cannot be normalized — a sample rate of `"0"`, a non-numeric duration, a
wrong-typed codec name — is an execution failure, not a `None`. Turning a broken
declaration into a missing one would manufacture an absence, and `core`'s own
validator reaches the same conclusion for the same reason.

**The adapter does not validate its own result.**
`validate_media_probe_result` runs in `core`, where the media processors call it,
and remains the final authority on the port contract. Re-running it here would be
a second normalizer competing with this one and would hide exactly the
inconsistency it exists to catch.

### Runtime failure is always `MediaProbeExecutionError`

Every way this adapter can fail to obtain a trustworthy result leaves as
`core.processing.media_probe.MediaProbeExecutionError`:

ffprobe cannot be launched; it timed out; it exited nonzero; a temporary file
could not be written, rewound or read; the output was not valid UTF-8; it was not
JSON; the top-level shape was wrong; a required structural field was missing; a
value could not be safely normalized; the output exceeded the adapter's budget.

It is never `ProcessingInputError`. The adapter has no name for that type at all,
so it cannot raise one. A verdict about the submitted media is not this layer's
to reach, and specifically must not be recorded as "examined and found to contain
no streams" when nothing managed to examine it.

**ffprobe's standard error is discarded unread**, sent to `DEVNULL` rather than
captured. Three reasons, in order of weight: it is not evidence, and parsing
another program's prose to decide whether somebody's file is valid would be
exactly the verdict this adapter must not reach; it is not a stable interface and
on some builds names paths on this machine; and discarding it makes the child's
output bounded by construction on that side. A nonzero exit is likewise not a
verdict — it can mean unreadable bytes, a refused protocol, a missing demuxer, a
crash, or an out-of-memory kill, and this runner cannot tell those apart.

The lifecycle ADR-022 fixed is therefore unchanged:

```
runtime probe failure -> MediaProbeExecutionError -> 503 media_probe_unavailable
                      -> capture stays PROCESSING, no ContentObject

trusted result failing container or stream policy -> ProcessingInputError
                      -> 422 -> capture FAILED
```

Both are exercised against a real server process in
`tests/integration/media/test_real_media_http.py`.

### Timeout and output budget

Two numbers, named in `unimem_media.policy` and frozen in tests:

- **30 seconds** of wall clock for one invocation. Generous for reading container
  headers, finite so a wedged child cannot hold a request open forever. On
  timeout `subprocess.run` sends `SIGKILL` and reaps, so no orphan is left
  holding the descriptor.
- **1 MiB** of standard output, read back as `1 MiB + 1 byte` so that "over
  budget" is distinguishable from "exactly at budget". One mebibyte is enormous
  for this query — a hundred-stream container is a few tens of kilobytes — so
  crossing it means the engine answered a different question than the one it was
  asked, and its answer is not trusted. Output past the budget is **refused, not
  truncated and parsed**: a truncated JSON document either fails to parse or,
  worse, parses into a smaller truth.

The output is written to a second private temporary file rather than accumulated
from a pipe, so nothing unbounded is held in this process at any point.

### The startup gate proves two things

`build_ffprobe_media_probe()` refuses to return a probe unless:

1. the configured `ffprobe` can be launched and reports a recognizable version;
2. **that build lists `fd` among the input protocols it can open.**

The second is not ceremony. FFmpeg builds are configurable and stripped-down ones
exist; a build without `fd` would accept this adapter's command line and fail on
the *first capture*, giving a server that starts, accepts audio at the door, and
then answers 503 to every one of them. Proving it at startup turns that into a
refusal with a sentence. Only the **input** section of `ffprobe -protocols` is
read: a build that could write `fd` but not read it cannot serve a capture.

Failure is `MediaPrerequisiteError`, an adapter-layer type. It is deliberately
**not** in `core`: which engine reads a container, and whether it is installed, is
a fact about a concrete adapter's deployment, and
`core.processing.media_probe` says so in as many words. It is equally not a
`MediaProbeExecutionError` — one says "do not start", the other says "one capture
could not be probed", and conflating them would make a misconfigured deployment
look transient.

The gate installs nothing, downloads nothing, contacts no service, and never
falls back to a different input protocol. Two fixed-argument subprocess calls with
`shell=False` and a finite timeout, and that is all.

**The version the gate obtained is discarded.** It exists for the message a
failed gate prints and for `describe_prerequisites()`; it is not passed to the
probe and never reaches canonical metadata. That is the opposite of the OCR
adapters, which *do* carry their engine version onto every content object — and
the difference is the point: an OCR engine's identity is part of what its output
means, and a demuxer's is not. ADR-021 fixed this and nothing here reopens it.

### One probe instance, shared, reentrant

`wiring._media_processors` hands the *same* `MediaProbe` to `AudioProcessor` and
`VideoProcessor`. That is only correct if the instance holds nothing on a
caller's behalf, so `FfprobeMediaProbe` holds two frozen values and nothing else:

- no mutable per-run attribute, and probing adds none;
- no global mutable process state;
- no shared temporary filename — each call creates its own pair of unlinked
  temporary files;
- no shared buffer;
- no lock, and none needed.

Every call owns its own subprocess and its own temporary resources. Tests drive
one instance from two threads with the children deliberately interleaved on a
barrier, and drive twelve concurrent probes of four different real files through
one instance, checking each gets its own answer.

This is why the adapter has no `Lock` where `unimem_ocr.tesseract` has one: that
lock exists to serialize a non-reentrant *native library* loaded into this
process, and there is no native library here — only a child process, which the
operating system already isolates from its siblings.

### `--media`, and the deployment surface

`python -m unimem_api --data-dir ./data --media` is the whole configuration
surface. One boolean on `Options`, no value, and no knob for the executable, the
timeout, the budget, or the containers.

Without it, the deployment is byte-for-byte what it was: `media_probe=None`,
intake refuses `AUDIO` and `VIDEO` at the door, no media processor is registered,
`unimem_media` is never imported, and no subprocess runs. Each of those is
checked in a real interpreter rather than by an AST scan, because "it does not
import it" is a claim about imports.

With it, the adapter is imported lazily inside `build_media_probe()`, the gate
runs, and the probe reaches the `media_probe=` argument `wiring.py` has accepted
since Phase 5A-2. **`wiring.py` is unchanged.** A missing prerequisite becomes

```
--media was requested but local media probing is unavailable: ...
```

as a `SystemExit`, before the data directory is created and long before the
socket is bound — the existing startup ordering, preserved because Python
evaluates the arguments to `build_local_app` first.

The capability is never silently disabled so the server can start anyway. Doing
so would hand a deployment that asked for audio the build that refuses it.

`--media`, `--pdf-ocr` and `--image-ocr` are independent. Every combination is a
valid deployment where its own prerequisites exist, each flag runs only its own
gate, and no gate is cached — a shared memo would make each capability's gate
depend on whether another happened to run first.

**There is no request field that enables media**, and there cannot be: it is
deployment configuration, and the only way to switch it is to restart the process
with a different command line.

### A bounded adapter is not a sandbox

This is stated plainly because three numbers can look like isolation.

What is bounded: one child process's wall-clock life; the bytes of its output
this process will read; and the memory held while staging the input, which is one
chunk rather than one file.

What is **not** bounded, and is not claimed to be: the child's resident set size;
any OS-level memory cgroup or `rlimit`; any CPU quota beyond the timeout; any
filesystem namespace, seccomp filter, or user change. The temporary *disk* the
staged copy occupies is the size of the submitted file, which this build already
accepted and stored. Nothing here guarantees that ffprobe cannot be killed by the
operating system under memory pressure, or that pathological but under-limit
input cannot cause large transient allocations inside the child. Nothing here
puts a deadline on the enclosing HTTP request, which has none.

Process isolation, cgroups, worker pools and containers are not introduced by
this phase. What the bounds do buy is real and worth having — a wedged ffprobe is
killed instead of waited on forever, an engine that decided to print a hundred
megabytes is refused instead of read, and an uploaded file cannot make the probe
open a URL — and it is not the same thing as a sandbox.

### Macro Phase 5A is not closed here

This repository's convention closes a macro phase with an **owner acceptance
run**, recorded in a `docs/MANUAL_*_ACCEPTANCE.md` document against a named merged
`main` commit. Macro Phase 4 was closed that way on 2026-09-18, and an earlier run
that scored 4 of 5 explicitly did *not* close it.

CI evidence is not that. The `Local media probing` job proves a great deal —
every supported family probed by a real ffprobe, a media capture reaching durable
`COMPLETE` content over a real socket, a restart reading it back unchanged, both
failure lifecycles, and an explicit assertion that none of it was skipped — but
it is not an owner acceptance record, and claiming closure on it would be
claiming evidence that does not exist.

**Macro Phase 5A therefore remains open**, for a later owner acceptance record.
Nothing in this slice is provisional; what is outstanding is the acceptance
ceremony, not the implementation.

## Consequences

Positive:

- An operator can run a media deployment: one flag, one system package, no Python
  extra.
- The default deployment is unchanged and still needs no media tooling, which the
  `Quality gates` job continues to demonstrate by having none.
- `core` stays testable on a machine with nothing installed, because the engine is
  behind the port and outside the kernel.
- An uploaded file cannot make the probe open a URL or a filesystem path, and no
  submitted string can reach the command line.
- A probe that fails cannot get a capture blamed for it, and a container mismatch
  still fails fast and terminally.
- One probe instance safely serves both processors and any number of concurrent
  captures.

Negative / costs:

- **The submitted file is copied to temporary storage for each probe.** That is
  real disk I/O proportional to the upload, and it is the price of a seekable
  source behind a stream-shaped port. It is bounded in *memory* but not in disk,
  and the disk it uses is the size of a file this build already stored.
- **A second system prerequisite joins Tesseract.** FFmpeg is a large package, and
  a deployment that wants media must install it; this project will not.
- **ffprobe's diagnostics are discarded**, so an operator debugging a stubborn
  file gets the adapter's classification rather than the engine's sentence. That
  is deliberate — the alternative is prose reaching a log that may name paths, and
  the temptation to parse it — but it is a genuine cost.
- **Bounded is not isolated**, as set out above. A deployment that needs real
  resource isolation must supply it around this process.
- **Macro Phase 5A stays open**, so the capability ships without a closure record.

## Alternatives considered

- **Widen `MediaProbe` to accept a path.** Rejected: it would put a
  caller-supplied filesystem instruction into the seam, which ADR-021 refused,
  and would make the raw store's layout part of the port.
- **Feed ffprobe through a pipe and skip the temporary file.** Rejected: not
  seekable, so containers with trailing indexes would be under-reported — a
  correctness failure disguised as a policy result.
- **Use `NamedTemporaryFile` and pass its path to ffprobe.** Rejected: it
  reintroduces a name to collide with, race, or leak, and it makes the
  no-path-in-argv property a convention instead of a fact.
- **Omit the protocol whitelist, since `fd:` is the only input named.** Rejected
  outright: a container can name an external resource, so the whitelist is the
  only thing standing between an uploaded file and an outbound request.
- **Use `r_frame_rate`.** Rejected: it is ffprobe's derived guess rather than the
  container's declaration, and ADR-021's field is documented as the latter.
- **Convert the frame rate to a float.** Rejected: `29.97` cannot be turned back
  into `30000/1001`.
- **Ask for tags and record them.** Rejected: ADR-021 fixed that this build reads
  no tags, and a title read out of a file is not a title somebody submitted.
- **Record the ffprobe version on the content object**, as the OCR adapters do.
  Rejected by ADR-021: a demuxer's identity does not change what a container says,
  unlike a recognizer's, whose identity is part of what its output means.
- **Keep the raw ffprobe JSON in metadata "for debugging".** Rejected: it is
  engine vocabulary, it is unbounded, and it would make every stored object
  dependent on one tool's output format.
- **Parse ffprobe's stderr to distinguish "corrupt file" from "engine broken".**
  Rejected outright: it is another program's prose, it is not a stable interface,
  and the verdict it would produce is precisely the one this adapter must not
  reach.
- **Truncate over-budget output and parse what fits.** Rejected: it either fails
  to parse or parses into a smaller truth, and the second is worse.
- **Add a `[media]` Python extra for symmetry with `[ocr]`.** Rejected: there is
  nothing for it to install, and its existence would imply `pip` could provide
  ffprobe.
- **Check only that ffprobe runs, not that it supports `fd`.** Rejected: the
  failure would surface on the first capture instead of at startup, which is the
  exact outcome a startup gate exists to prevent.
- **Cache the prerequisite probe across capabilities.** Rejected for the reason
  ADR-018 and ADR-020 already give: it would make each gate depend on whether
  another ran first.
- **Give `--media` an option for the executable, the timeout or the budget.**
  Rejected: the policy is a build constant, and a per-deployment knob that changes
  what gets recorded is a second unreviewable policy.
- **Add a capability registry now that there are three optional capabilities.**
  Rejected, as in ADR-022: three booleans in one function signature is not a
  population, and the registry would be the abstraction arriving before the
  requirement.
- **Invoke `ffmpeg` to normalize awkward containers before probing.** Rejected:
  it would transcode somebody's original, which ADR-021's original-first rule
  forbids, and nothing under `src/` invokes ffmpeg for any reason.
- **Close Macro Phase 5A on the strength of the new CI job.** Rejected: the
  repository closes macro phases with an owner acceptance run, and CI is not one.
