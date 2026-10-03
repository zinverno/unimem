# Live YouTube delivery repeat — 2026-10-03

Source: the locally available `/tmp/unimem-youtube-diagnostic-20261003/REPORT.md`,
read before implementation of local audio transcription. This is a redacted
summary; private profiles, credentials, response bodies and diagnostics are not
included. The observed source build was `16d341356482dfd56cc0ace79a16d743ec844119`;
PR #34 was already merged. The inspected production files were unchanged at
main `0987a8eba006de0e1e1e587a10eb1ac360c6da62`.

## Successful repeat, without a production change

The installed Zen extension submitted a fresh production caption operation.
The actual API/worker completed in **2.23 seconds**, selecting an English manual
track with six timed segments. An explicit Send delivered one note through the
standalone UniMem Connector in an isolated Obsidian vault. Refresh and repeated
poll left one operation, capture, content, delivery and note; journal ACK was
persisted. Preview, immutable snapshot and vault file matched exactly:
**1,529 bytes**, SHA-256
`a614a4c9634104c8dfc13cda2ab6dc1a159f15b8d8a284df35f47e3dacc9010d`.

One preceding direct acquisition used production `BoundedSession`: watch,
player and timedtext all returned HTTP 200, including completed timedtext body
reading. Total wall time was **1.31 seconds**, with **1,369,905 response-body
bytes** read. No exception or budget fired. Together these were two full
acquisitions, with no separate control network probes.

The run retained TLS verification, `trust_env=False`, empty proxies, socket
timeouts `(5,10)`, 45-second acquisition and 75-second worker budgets. Python
3.13.13, Zen 1.21.8b, Obsidian 1.13.7, browser extension 0.3.0 and Connector
0.1.0 were used. UI actions were automated through the installed applications;
this does not claim a physical user gesture. The targeted retrieval/transport
suite passed **30 tests**. No production diff was needed.

## Historical timeout remains unexplained

The [2026-10-01 BLOCKED attempt](OBSIDIAN_PERSIST_VERIFICATION.md#real-youtube-e2e-blocked-by-acquisition-timeout)
and its failed operation remain unchanged. That receipt contains no endpoint,
headers/body boundary or original exception class. Its 11.5-second duration
cannot identify a connect, read or elapsed timeout. This repeat demonstrates
success, not a diagnosed or repaired historical cause.

## Separate evidence and remaining gates

The earlier [synthetic acceptance](OBSIDIAN_VERIFICATION.md) remains synthetic.
This dated repeat is the separate real acquisition and delivery **PASS**.
It does not close the remaining [B4 browser gates](BROWSER_VERIFICATION.md):
normal system save chooser, outstanding other-browser coverage, signed install,
restart/update and distribution. Windows/macOS native filesystem and earlier
media owner gates also remain open. Nothing was published or released.
