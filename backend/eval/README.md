# Docparse Quality Evaluation Harness

This evaluation harness generates a representative local corpus, runs each
document through Docparse, scores the output quality, and produces both JSON
and Markdown reports.

Use it to:

- spot weak titles, generic classifications, and brittle summaries
- catch malformed action items and inconsistent metadata
- compare a refinement run against a baseline before keeping changes

Typical flow:

```bash
cd /Users/yoonjaeseong/Desktop/projects/Docparse/backend
./.venv/bin/python scripts/run_quality_eval.py --mode fallback --label baseline
# make a focused refinement
./.venv/bin/python scripts/run_quality_eval.py --mode fallback --label refined --compare-to eval/reports/latest-fallback.json
```

Gemma-mode flow:

```bash
cd /Users/yoonjaeseong/Desktop/projects/Docparse/backend
./.venv/bin/python scripts/run_quality_eval.py \
  --mode gemma \
  --backend-url http://localhost:8001 \
  --label gemma-check \
  --compare-to eval/reports/latest-gemma.json
```

For CPU GGUF development, use a small live slice first:

```bash
./.venv/bin/python scripts/run_quality_eval.py \
  --mode gemma \
  --backend-url http://localhost:8001 \
  --label gguf-smoke \
  --limit 2 \
  --cleanup
```

Or target a specific case while refining:

```bash
./.venv/bin/python scripts/run_quality_eval.py \
  --mode gemma \
  --backend-url http://localhost:8001 \
  --label gguf-policy-memo \
  --ids long_policy_memo_md \
  --cleanup
```

The harness stores:

- generated docs in `backend/eval/corpus/`
- current run reports in `backend/eval/reports/latest*.json` and `backend/eval/reports/latest*.md`
- historical timestamped reports in `backend/eval/reports/archive/`
- representative expectations in `backend/eval/specs/eval_documents.json`

The expectations are intentionally coarse. They do not hardcode exact text;
instead they define quality signals such as expected profiles, broad types,
keyword coverage, and anti-patterns to avoid.

## Modes

### `--mode fallback`

Runs the representative corpus through the in-process evaluation path. This is
fast and useful for regression checks even when Gemma is unavailable.

### `--mode gemma`

Runs the representative corpus through the live backend API:

1. uploads each generated evaluation document to `/api/documents/upload`
2. polls `/api/documents/{id}` until processing completes
3. verifies the final provider chain actually contains Gemma interpretation
4. scores the final user-facing output

Gemma mode is intentionally strict:

- it fails if Gemma mode is requested but provider-chain evidence does not show
  Gemma participation
- it warns on fallback-like summaries, weak important points, copied-looking
  action items, or under-explained detailed summaries

### Gemma mode prerequisites

Before running Gemma mode, make sure:

- Postgres is running
- the backend API is running
- Gemma is configured, for example with `GEMMA_MODEL_DIR` or
  `HUGGINGFACE_TOKEN`, or with `AI_INTERPRETATION_PROVIDER=llama_cpp` and
  `LLAMA_CPP_MODEL_PATH` pointing at a Gemma 3 GGUF file
- the backend is actually using Gemma interpretation rather than the
  unavailable fallback path

Example:

```bash
cd /Users/yoonjaeseong/Desktop/projects/Docparse/backend
source .venv/bin/activate
export GEMMA_MODEL_DIR=/Users/yoonjaeseong/Desktop/models/gemma-2-2b-it
export AI_INTERPRETATION_PROVIDER=gemma
export AI_INTERPRETATION_FORCE_SMALL_MODEL=true
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

GGUF example:

```bash
cd /Users/yoonjaeseong/Desktop/projects/Docparse/backend
source .venv/bin/activate
export AI_INTERPRETATION_PROVIDER=llama_cpp
export LLAMA_CPP_MODEL_PATH=../models/gguf/gemma-3-4b-it-q4_0.gguf
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Then, in another shell:

```bash
cd /Users/yoonjaeseong/Desktop/projects/Docparse/backend
./.venv/bin/python scripts/run_quality_eval.py --mode gemma --backend-url http://localhost:8001 --label gemma-local
```
