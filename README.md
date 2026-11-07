# aircraft-track-association

Govia ML Engineer take-home: associate fragmented aircraft tracklets into per-aircraft tracks.

## Layout

- `candidate-packet/` - the original assessment data package, copied verbatim (see `candidate-packet/README.md`).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cd candidate-packet && uv run python verify_package.py
```
