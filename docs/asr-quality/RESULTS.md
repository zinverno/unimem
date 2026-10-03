# Base versus small: measured result, 2026-10-03

## Decision

**Keep the existing base profile; no production change in this PR.** Small has real
benefits on this set, including the heldout utterance. They are reported below,
not dismissed as noise. However, it does not meet the predeclared discovery
semantic gate: the transfer command gains an unsupported name and still loses
its action/recipient. A pooled WER improvement alone is insufficient for this
promotion decision. This is a conservative decision on six short inputs, not a
claim that base is more accurate or that small is generally unhelpful.

The [preliminary decision](DECISION.md) was committed as `6176fb3` before the
heldout run; the [sample/metric/settings freeze](PROTOCOL.md) is `2b46c43`.
No tuning, third model, replacement sample or production selector was added.

## Accuracy (S / D / I; raw normalization, no number or abbreviation repair)

| Sample | Duration s | N | Base S/D/I | Base WER | Small S/D/I | Small WER |
|---|---:|---:|---|---:|---|---:|
| ru-literary  | 11.350 | 24 | 7/1/1 | 37.50% | 9/0/0 | 37.50% |
| ru-negation  | 3.688 | 8 | 1/0/0 | 12.50% | 0/0/0 | 0.00% |
| ru-transfer  | 3.106 | 6 | 4/0/0 | 66.67% | 3/0/0 | 50.00% |
| ru-name (heldout) | 2.295 | 4 | 2/0/0 | 50.00% | 0/0/0 | 0.00% |
| en-prose  | 5.855 | 17 | 1/0/0 | 5.88% | 1/0/0 | 5.88% |

**Discovery Russian:** 14/38 = 36.84% base; 12/38 = 31.58% small.
**All four Russian utterances, including heldout:** 16/42 = 38.10% base;
12/42 = 28.57% small. Heldout is also shown separately; it was not used to tune
any parameter. Do not pool silence or auto-language repetitions into these totals.

### References and unedited hypotheses

**ru-literary**

Reference: Для вас, души моей царицы, Красавицы, для вас одних Времен минувших небылицы, В часы досугов золотых, Под шепот старины болтливой, Рукою верной я писал

base: Для вас тушимая царицы, красавицы, для вас одних, времен минувших не полиции в часы досугов золотых подшоп от старинной болтливой рукой верно я писал.

small: Для вас, туши моей царицы, красавицы, для вас одних, времен минувших небылицы, в часы до сугов золотых подшепот старинный болтливый рукой верно я писал.

**ru-negation**

Reference: афина я не хочу чтобы сегодня звонил будильник

base: Афина, я не хочу, чтобы сегодня звонил Гудвинник.

small: Афина, я не хочу, чтобы сегодня звонил будильник.

**ru-transfer**

Reference: сбер привет переведи маме тысячу рублей

base: Сбир, привет! Передим они тысяч рублей!

small: Збир, привет! Перейди, Маня, тысячу рублей.

**ru-name**

Reference: салют позвони владимиру петровичу

base: Солю позвоню Владимиру Петровичу.

small: Салют позвони Владимиру Петровичу.

**en-prose**

Reference: MISTER QUILTER IS THE APOSTLE OF THE MIDDLE CLASSES AND WE ARE GLAD TO WELCOME HIS GOSPEL

base: Mr. Quilter is the apostle of the middle classes, and we are glad to welcome his gospel.

small: Mr. Quilter is the apostle of the middle classes, and we are glad to welcome his gospel.

### Consequential errors and heldout

- Literary: base invents `не полиции` for `небылицы`, merges/damages `души моей`
  and `под шепот`, and changes inflections. Small restores `небылицы`, but says
  `туши` for `души`, splits `досугов` into `до сугов`, merges `под шепот`, and
  retains wrong inflections. Equal 37.5% WER does not mean identical errors.
- Negation: both retain `не хочу`. Base replaces the object `будильник` with
  `Гудвинник`; small is exact after punctuation normalization.
- Transfer: neither preserves `переведи маме`. Base says `Передим они тысяч`,
  small says `Перейди, Маня, тысячу`. Small restores the numeric word form but
  introduces an unsupported name; both lose actionable meaning. No number
  equivalence adjustment was applied to WER, and the base malformed amount is
  not treated as a valid alternative amount.
- **Heldout:** small is exact (0/4); base changes `Салют позвони` to
  `Солю позвоню` (2/4), changing an imperative into first-person future. Both
  preserve `Владимиру Петровичу`. This is positive evidence for small, but does
  not retroactively make the discovery semantic gate pass. No further settings
  were tried after this result.
- English: identical output; `Mr.` versus reference `MISTER` is the sole WER
  substitution, an orthographic abbreviation rather than a meaning error.
  Names and meaning are preserved; primary WER remains 1/17.
- No complete speech utterance was omitted. Base literary alignment has one
  deletion and one insertion; merged/split words influence edit counts. Small
  has lexical hallucinations/substitutions, not an extra unrelated sentence.
- Both VAD-gated runs return `no_speech` and zero segments for digital silence.
  This does not prove absence of speech or immunity to noise/music hallucination.
- Separate `auto` on ru-negation detected `ru` for both and returned exactly
  their respective explicit-ru hypotheses (12.5% / 0%). Explicit `ru` was
  already used historically; language selection is not the literary fix.

## Input and integration audit

No decoding/integration defect was found on these inputs. All are complete mono
16 kHz PCM s16le files. Source-to-prepared PCM arrays are byte-identical for all
four new public files; only WAV metadata changed where hashes differ. Every
production-decoded float array equals the entire stdlib WAV sample array / 32768,
including its first/last samples. Sample counts and durations agree exactly.
No clipping samples were found. Peak absolute PCM is 0.4240 literary, 0.02023
negation, 0.02036 transfer, 0.01324 heldout name and 0.38794 English. The farfield
commands are quiet in their source recordings; gain was not changed for either
model. This does not test stereo downmix or other resampling rates.

All returned segment times are finite, ordered, nonnegative and within source
duration. No fabricated word timings were added. They are plausible segment
boundaries, not independently hand-aligned word timing accuracy. Raw timestamps
are in [runs.json](runs.json). Direct listening was unavailable; exact corpus
pairing and its independent annotation/alignment process were checked instead.
Human listening verification of these particular files remains a limitation.

The six **actual saved base outputs** were passed through the existing capture
and persistence path. Text (after the existing outer-space trim) and segment
order/times were unchanged. A second read/render succeeded with model lookup,
ASR and raw audio opens replaced by failing guards. [Canonical audit](canonical-audit.json)
records exact source and rendered digests. This is a boundary audit of real
outputs, not another ASR run or evidence of text quality from matching hashes.

## Resources

AMD Ryzen 5 5500U, 6 cores / 12 logical CPUs, 7,429,008 KiB RAM (~7.09 GiB),
Linux 6.12.108-1-MANJARO; Python 3.13.13. faster-whisper 1.2.1, CTranslate2 4.8.2,
PyAV 16.1.0, ONNX Runtime 1.30.0, NumPy 2.5.3. CPU/int8, two inference threads,
one worker, decoder one thread; OMP/OpenBLAS limited to two. No CUDA or paid API.

Small is [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small),
revision `536b0662742c02347bc0e980a01041f333bce120`, MIT, multilingual. Engine:
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). Exact four-file hashes
for both models are in [models.json](models.json); small preparation downloaded
~486 MB once (progress reported ~85 s). Weight-download peak RSS was not measured.
Every subsequent inference was offline: local paths/local_files_only, HF offline,
Python socket audit guard (zero attempts), execution in the network-restricted
sandbox. The timing table separates local file hash verification from model
initialization. No remote inference occurred.

| Input / language | Model | Verify s | Decode s | Model init s | ASR s | Process total s | Peak RSS MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| ru-literary / ru | base | 1.069 | 0.012 | 0.544 | 2.783 | 5.371 | 362.1 |
| ru-literary / ru | small | 1.147 | 0.010 | 1.553 | 8.091 | 11.207 | 646.7 |
| ru-negation / ru | base | 0.097 | 0.008 | 0.448 | 2.171 | 3.093 | 357.5 |
| ru-negation / ru | small | 1.140 | 0.008 | 1.158 | 6.368 | 9.098 | 645.6 |
| ru-transfer / ru | base | 0.099 | 0.007 | 0.375 | 2.121 | 2.994 | 357.3 |
| ru-transfer / ru | small | 0.322 | 0.007 | 0.959 | 6.503 | 8.212 | 645.9 |
| ru-name / ru | base | 0.661 | 0.006 | 0.606 | 2.032 | 3.671 | 357.0 |
| ru-name / ru | small | 0.569 | 0.007 | 1.214 | 6.099 | 8.331 | 645.4 |
| en-prose / en | base | 0.097 | 0.008 | 0.376 | 2.217 | 3.094 | 358.0 |
| en-prose / en | small | 0.323 | 0.008 | 1.017 | 6.614 | 8.364 | 646.2 |
| silence / ru | base | 0.099 | 0.008 | 0.401 | 0.027 | 0.912 | 357.8 |
| silence / ru | small | 0.321 | 0.007 | 0.966 | 0.029 | 1.747 | 645.3 |
| ru-negation / auto | base | 0.701 | 0.007 | 0.346 | 1.999 | 3.447 | 357.4 |
| ru-negation / auto | small | 0.743 | 0.007 | 1.026 | 6.042 | 8.264 | 645.2 |

ASR includes VAD, language detection, generator exhaustion and output validation.
Total includes interpreter/import/measurement overhead; stage times need not sum
exactly to process wall time. Each row is one new process, sequential, not an
average or a controlled cold-cache benchmark. Hash times show filesystem cache
variation. Peak RSS is child getrusage, with a separate 25-ms parent RSS monitor.
All 14 runs completed within the unchanged 300 s / 3 GiB address-space / 1.5 GiB
RSS budgets. No limit was relaxed. Short commands are slower than their recording
with small; do not promise faster-than-real-time transcription or extrapolate to
120-second files. Small is ~3× base recognition time here, ~645–647 MiB versus
~357–362 MiB peak RSS. No long-file small budget acceptance was performed.

## Boundaries and remaining work

Production profile/operation/provenance schemas, pipeline, browser, Connector,
delivery snapshots and ACK are unchanged. Existing automatic-transcription
warning remains. No new dev build, native E2E, note import or release was made:
there is no production profile change requiring another native run. Prior PR #35
Zen → Connector → one Obsidian note evidence remains historical and is not
relabelled as a small-profile acceptance. Older B4 gates remain open.

This tiny fixed sample cannot establish quality across Russian voices, accents,
noise, music, spontaneous dialogue or long messages. The commands are elicited
farfield speech; English is read prose. Corpus references can contain errors and
were not newly listened to here. No independent speaker-diversity claim is made.
Historical 37.5% and loop-based resource evidence remain unchanged.

Reproduction commands and check results: [README.md](README.md).
