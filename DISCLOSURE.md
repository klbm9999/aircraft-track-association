# Disclosure

> Template: replace every `[FILL: ...]` and delete this note before submitting.

## Time spent

Approximately [FILL: hours] hours in total.

| Activity | Approximate time |
| --- | --- |
| Reading the brief, challenge, clarifications and data package | [FILL] |
| Data investigation and preprocessing | [FILL] |
| Baseline and Gaussian continuity model, including tuning | [FILL] |
| Learned approach | [FILL] |
| Evaluation, stress test and failure analysis | [FILL] |
| Backend and web interface | [FILL] |
| Documentation | [FILL] |

## AI assistance

AI coding assistants were used for this submission.
I directed the work, made the design decisions, and reviewed and validated the results; I am responsible for all of it.

### Claude Code (this repository, except the learned approach)

- Tool: Claude Code (Anthropic), model Claude Opus 5.5.
- I chose the approach for the Gaussian continuity model: deduplication, metric positions and altitude, position and velocity Gaussians, Hungarian matching with a no-match option, and the cost terms (projected-position plausibility, Wasserstein distances, cosines, gap penalty, altitude). I also specified the baseline rule, the field interpretations to check, the weight-search and stress-test tasks, and the failure analysis to report.
- Claude Code wrote most of the code, tests, README and REPORT under that direction. It ran the evaluations, proposed and ran diagnostics (for example the velocity-window fix and the finding that the 3D heading term was implicitly using altitude), built the API and web interface, and checked the interface in a browser.
- How I validated the output: [FILL: e.g. reviewed diffs, reran evaluations, inspected the interface, checked numbers in the report against command output].

### [FILL: tool name] (learned approach)

- The learned approach was coded with AI assistance in a separate [FILL: tool / chat] session.
- [FILL: what you specified, what the assistant produced, and how you validated it.]

### Other assistance

[FILL: any other AI or human assistance, or "None".]

## Reused code and material

- `candidate-packet/` is the supplied data package, copied unchanged. `candidate-packet/SOURCE_NOTES.md` was transcribed from the supplied `Source Notes.docx`, because `verify_package.py` expects a Markdown file; its checksum does not match the package's `checksums.json`.
- Open-source libraries are listed in `pyproject.toml` and pinned in `uv.lock`.
- [FILL: any other reused code, or "No other code was reused."]

## Unfinished work

- [FILL: anything unfinished at submission, e.g. the learned approach's integration into the API and interface, if not done.]
- See the Status and Known limitations sections of `README.md` for the state of the rest of the system.

## Prompt log

All prompts submitted to AI tools for this assessment, including follow-ups, are in [FILL: file name, e.g. `PROMPTS.md`].
AI responses are not included.
No credentials or unrelated private information are included.
