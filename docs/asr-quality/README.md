# Reproduce the fixed base/small comparison

Decision, hypotheses, WER and resource table: [RESULTS.md](RESULTS.md).
Pre-run rules: [PROTOCOL.md](PROTOCOL.md). References/hashes: [samples.json](samples.json).
Raw segments, validated results, timings and scores: [runs.json](runs.json).
No audio or weights are committed. Tested with Python 3.13.13, optional `.[dev,asr]`
and FFmpeg n9.0.1 (source WAV metadata normalization only). See [installation](../LOCAL_AUDIO.md).

Preparation is explicit network IO, separate from recognition:

```sh
ASR_EVAL=/tmp/unimem-asr-evaluation-NEW
mkdir -p "$ASR_EVAL"
.venv/bin/python scripts/asr_quality_prepare.py audio "$ASR_EVAL/audio"
# Existing pinned base, if not already prepared:
.venv/bin/python -m unimem_asr prepare --model-dir "$ASR_EVAL/base" --download
# Only additional candidate: multilingual small, MIT; ~486 MB download.
# Evaluation retains 2 CPU threads, 3 GiB address space, 1.5 GiB RSS and 300 s.
HF_HOME="$ASR_EVAL/hf-cache" .venv/bin/python scripts/asr_quality_prepare.py small "$ASR_EVAL/small"
```

The helper downloads individual assets, checks fixed corpus revision, paired
annotation and SHA-256. Changed upstream data causes refusal. Expiring viewer
URLs stay in memory. Licenses and attribution are in the manifest/protocol.
Neither complete corpora nor another model are downloaded.

Recognition observes the production engine with a research-only verifier for
the two pinned manifests. Production remains base-only. Each `run` starts a new
bounded process and refuses to overwrite a result. Run sequentially:

```sh
for candidate in base small; do
  for sample in ru-literary ru-negation ru-transfer en-prose silence; do
    language=ru
    if [ "$sample" = en-prose ]; then language=en; fi
    .venv/bin/python scripts/asr_quality.py run \
      --sample "$sample" --candidate "$candidate" --language "$language" \
      --audio "$ASR_EVAL/audio" --model "$ASR_EVAL/$candidate" \
      --output "$ASR_EVAL/results/$candidate-$sample.json"
  done
done
```

The recorded decision predates heldout outputs. Reproduce the final check and
separate language check without tuning. A new investigation needs its own
protocol/decision before opening heldout results; this historical set is no
longer blind.

```sh
for candidate in base small; do
  .venv/bin/python scripts/asr_quality.py run \
    --sample ru-name --candidate "$candidate" --language ru \
    --audio "$ASR_EVAL/audio" --model "$ASR_EVAL/$candidate" \
    --output "$ASR_EVAL/results/$candidate-ru-name-ru.json"
  .venv/bin/python scripts/asr_quality.py run \
    --sample ru-negation --candidate "$candidate" --language auto \
    --audio "$ASR_EVAL/audio" --model "$ASR_EVAL/$candidate" \
    --output "$ASR_EVAL/results/$candidate-ru-negation-auto.json"
done
# Persist actual recorded base outputs; audit text/times and offline reread/render.
.venv/bin/python scripts/asr_quality.py audit \
  --audio "$ASR_EVAL/audio" --results "$ASR_EVAL/results" \
  --output "$ASR_EVAL/canonical-audit"
```

Audit capture IDs/timestamps are fresh, so Markdown digests vary between separate
captures. Within each audit reread/render is exactly identical. This property is
separate from WER and semantic correctness.

```sh
.venv/bin/pytest tests/unit/test_asr_quality.py -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov=core --cov=unimem_api
```

The metric check needs no native ASR or weights. It covers meaning-bearing
deletion, substitution/insertion, edit ties, Russian normalization, numbers and
abbreviations remaining distinct, and empty references. Real recognition is the
separate 14-run experiment, not mocked CI. The preparation helper was exercised
in a fresh directory: all six hashes reproduced. Existing CI gates are unchanged;
final status is reported on the dependent PR. No merge or release.

Local validation: **4385 PASS**, coverage **98.28%** (90% floor unchanged);
ruff check/format PASS, mypy PASS (273 source files).
