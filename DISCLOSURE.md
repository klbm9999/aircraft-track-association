# Disclosure

## Time spent

Approximately 6 hours in total.

| Activity | Approximate time |
| --- | --- |
| Reading the brief, challenge, clarifications and data, data prep | 1hr |
| Baseline and Gaussian continuity model, including tuning | 5hrs |
| Learned approach | 2hr (parallel) |
| Evaluation, stress test and failure analysis | 30mins |
| Backend and web interface | 30mins |
| Documentation | minutes |

## AI assistance

AI coding assistants were used for this submission.
I directed the work, made the design decisions, and reviewed and validated the results; I am responsible for all of it.

### Claude Code (this repository, except the learned approach)

- Tool: Claude Code (Anthropic), model Claude Opus 5.5.
- I chose the approach for the Gaussian continuity model: deduplication, metric positions and altitude, position and velocity Gaussians, Hungarian matching with a no-match option, and the cost terms (projected-position plausibility, Wasserstein distances, cosines, gap penalty, altitude). I also specified the baseline rule, the field interpretations to check, the weight-search and stress-test tasks, and the failure analysis to report.
- Claude Code wrote most of the code, tests, README and REPORT under that direction. It ran the evaluations, proposed and ran diagnostics (for example the velocity-window fix and the finding that the 3D heading term was implicitly using altitude), built the API and web interface, and checked the interface in a browser.
- How I validated the output: check the outputs, weights, costs and parameters to guide the next step.

### Claude code (learned approach)

- The learned approach was coded with AI assistance in a separate claude code session using Firstmate agent orchestration tool.
- I specified to generate some attack approaches, a preliminary data analysis and to come up with a plan, a gradient boosted tree approach was selected from there, and the agent went on to build, train, evaluate the model. Its results are included in REPORT.md

### Other assistance

## Reused code and material

- `candidate-packet/` is the supplied data package, copied unchanged. `candidate-packet/SOURCE_NOTES.md` was transcribed from the supplied `Source Notes.docx`, because `verify_package.py` expects a Markdown file; its checksum does not match the package's `checksums.json`.
- Open-source libraries are listed in `pyproject.toml` and pinned in `uv.lock`.
- ChatGPT was used as a conversational tool to refine my gaussian approach, unfortunately it was done in temporary mode and the logs cannot be recovered. It was verbal conversation.

## Unfinished work

- The weight parameter search for the gaussian approach can be further improved by strategies instead of being a random seeded search or grid search. That is an optimisation problem with existing approaches but not enough time to try them out.
- See the Status and Known limitations sections of `README.md` for the state of the rest of the system.

## Prompt log

All prompts submitted to AI tools for this assessment, including follow-ups, are in the repo.
No credentials or unrelated private information are included.
