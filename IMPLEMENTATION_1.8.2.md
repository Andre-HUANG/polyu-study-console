# 1.8.2 implementation and validation
Approved scope: performance/energy improvements, timetable (classes and exams with evidence and manual confirmation), official PolyU academic calendar, signed GitHub updates for macOS arm64 and Windows x64 Setup/Portable.

Baseline: 1.8.1 commit 11a17fb. Isolated branch codex/v1.8.2; preserve prior source and installers.
The previous analysis ran 160 offline tests successfully. Live installed app was 1.8.0; do not treat its measurements as a 1.8.1 power acceptance.
All release gates, native tests, signed update rehearsals and real Canvas checks must be recorded independently before publication.


User layout revision: software updates are in Settings. Overview, timetable and
calendar share aligned first-row panels; recent arrangements occupy the next row.
Native GUI layout assertions pass at wide and narrow window sizes.

Current verified boundaries: read-only real Canvas identity and calendar query
succeeded for all 9 saved courses, returning no calendar entries. File schedule
recognition remains available, with group/confidence ambiguity pending.

The native Sparkle 2.10.0 isolated rehearsal passed download/signature/install/
restart/rollback, corrupt package, wrong key and network failure. It found and
fixed a rollback launcher race. The old shell/writer handshake and real-user
account migration require their own evidence; isolated tests do not replace them.
Windows WinSparkle 0.9.4 native rehearsals are required in disposable-runner CI.

Synthetic recognition comparison: 46 files / 102 pages, identical baseline and
candidate reader binary. Golden output preserves dates, weights, locators,
quotes and confidence; only the PDF method label changes to describe OCR reuse.
Warm index writes fall from 15 to 1. Cold/warm timing and native idle CPU are
measured separately; no physical battery-energy measurement is claimed.
