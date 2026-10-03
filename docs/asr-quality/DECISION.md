# Preliminary decision — before heldout, 2026-10-03

Retain base; do not add a production small selector on this evidence. Settings
remain exactly those frozen in PROTOCOL.md; no parameter search or third model.

Discovery Russian: base 14/38 word edits (36.84%); small 12/38 (31.58%). Small
corrects `будильник` and some literary words, but literary WER remains 9/24.
On the transfer command both lose the requested action and recipient. Small's
`Перейди, Маня, тысячу рублей` introduces an unsupported name (`Маня`) in place
of `маме`; restoring `тысячу` does not restore the command's meaning. This is
mixed evidence, not the predeclared improvement across multiple utterances
without new consequential substitutions. English output is identical. Both
return no speech on the one digital silence.

Small does fit the unchanged process budgets: observed peak ~647 MiB, against
~362 MiB base; speech-run wall times 8.21–11.21 s versus 2.99–5.37 s. Resource
fit is not the reason for rejection. The modest pooled WER reduction, mixed
semantic errors and extra cost do not establish enough benefit for another
supported production profile on this tiny set.

Now run the fixed heldout name utterance once with each model, and the separate
auto-language check on the negation utterance. These are confirmation/limitation
evidence, not an opportunity to change settings or replace samples. Final results
will be appended in RESULTS.md, leaving this preliminary decision intact.
