# Fixed comparison, 2026-10-03

Frozen before either model is evaluated on this set. Parent: PR #35,
`de3840e653179caa6a09faea62398c2e1ec175de`; research branch is separate.

## Samples and references

`samples.json` fixes six complete utterances, their boundaries, references and
SHA-256. No trimming, gain change, repetition, reference prompts, hotwords or
postprocessing. New Russian samples were selected by reference content **before
any model run**: negation (Golos test row 3), a monetary amount (row 8), and a
name (row 4, held out). The historical literary sample is unchanged. English is
ordinary prose read aloud, not spontaneous conversation. Silence is generated
PCM zero. No OGG copy is counted as another independent recording.

Reference verification uses the corpus' exact audio/text pairing and complete
utterance boundaries, not a book passage copied independently of segmentation.
Golos reports human annotation and audio/text validation (five validators must
agree for its crowd validation stage); see [the paper, sections 3.3–3.4](https://arxiv.org/html/2106.10161).
The mirror identifies the test split as the original farfield test split.
[LibriSpeech](https://www.openslr.org/12/) publishes segmented/aligned utterances;
English ID is `1272-128104-0000`. Historical Russian uses its previously fixed
segment annotation. Direct listening is unavailable in this execution environment:
this is verification against independently published annotations, **not a new
human listening audit**. Annotation errors remain a limitation.

Golos commands have conversational wording but were elicited from templates;
they do not establish quality on spontaneous personal voice messages. MINDS14
was considered and rejected **before recognition** because its references are
Google ASR output ([authors, section 4](https://arxiv.org/html/2104.08524)).
No personal recordings or microphone were accessed.

Only four individual public audio assets were downloaded for the final new set.
Viewer WAVs were losslessly rewritten as PCM s16le with metadata stripped;
original and input hashes are recorded separately. Check PCM samples, sample
count, beginning/end, amplitude and original/decoded duration before inference.
All selected inputs are mono 16 kHz: stereo mixing/resampling quality is outside
this comparison. No entire corpus, model other than small, or third ASR is used.

Golos reference excerpts retain its [Public license with attribution and
conditions reserved](https://github.com/sberdevices/golos/blob/master/license/en_us.pdf),
including its disclaimer of warranties, rather than the repository code license.
Attribution is in the manifest. Text is unchanged; audio container metadata alone
was stripped. English excerpts retain CC BY 4.0 attribution; Russian literary
recording is from public-domain LibriVox. No audio is committed.

## Comparison and decision

A: faster-whisper 1.2.1, Systran/faster-whisper-base revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`.
B: same engine, Systran/faster-whisper-small revision
`536b0662742c02347bc0e980a01041f333bce120` (MIT, multilingual).
Four local model files are hashed and verified before every run. Explicit
preparation downloads only those files; inference has no network access.

Both: CPU/int8, two inference threads, one worker, beam 5, temperature 0,
condition_on_previous_text=False, VAD min_silence_duration_ms=500, transcription.
Russian explicitly `ru`, English `en`. Separate auto-language check on
`ru-negation`, with both models; do not pool it into main WER.

Use the existing production decoder and engine, with evaluation-only observation
and a pinned small verifier; do not alter the production model allowlist to run
research. Fresh process per run, sequential execution, 300-second wall/CPU budget,
3 GiB address-space limit and 1.5 GiB monitored RSS cap. Record verification,
decode, model initialization, recognition (including VAD/language detection),
total elapsed time and peak RSS. Failed budget runs have no quality score.

WER normalization, fixed now: Unicode lowercase, ё→е; split on every non-word
character (underscore also separator), retaining letters and digits. No lexical
substitutions, stemming, number expansion, abbreviation expansion, or private
exception list. Levenshtein word alignment; equal-cost ties prefer substitution,
then deletion, then insertion. Report S/D/I and N. Aggregate = sum(S+D+I)/sum(N),
never mean of sample percentages. Silence, auto reruns and any converted duplicates
are excluded. Number spelling equivalence is discussed separately, not substituted
into the primary score.

Consider adding small only if it reduces consequential errors on at least two
of the three discovery Russian utterances, improves their pooled WER, preserves
negations/amounts without new material reversals, and fits the existing budgets.
Record that preliminary decision before opening heldout `ru-name` results.
Then run both candidates once on heldout, without changing settings; a material
regression or resource failure prevents promotion. Otherwise an explicit optional
profile is justified for this narrow set, with base still default. An inconclusive
or negative result means no production selector and no third candidate.

Old measurements remain historical: 9/24 errors (37.5%) on one literary sample;
the 113.5-second loop is not independent quality evidence. No result here closes
the older B4 gates or proves general Russian voice-message accuracy.
