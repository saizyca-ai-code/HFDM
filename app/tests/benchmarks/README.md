# HFDM V2 transfer benchmarks

These opt-in harnesses compare the Phase 4 Model, Dataset, and Civitai workloads without writing HFDM's production database or `download/` directory. Immutable public identities and expected selections live in `manifest.json`; every live run rejects metadata drift before downloading.

## Safety and preparation

- Use a dedicated destination outside the repository and production portable directory. Harnesses never delete it.
- Plan for 55–60 GiB of network transfer and at least 70 GiB free space for the confirmed matrix.
- Use `--dry-run` first, then `--resolve-only` to validate current public metadata without transferring model content.
- Record the real destination media with `--disk-type ssd` or `hdd`, plus a non-secret `--network-label`.
- Public fixtures need no token. If replacements require credentials, provide process-only `HF_TOKEN` or `CIVITAI_TOKEN`; never pass tokens as arguments.
- A cold run needs a fresh provider cache and an empty destination. Cache preparation is intentionally manual and non-destructive.

## Fixed workloads

- `hf-model-large`: Whisper Large V3 Turbo `model.safetensors`, 1,617,824,864 bytes.
- `hf-dataset-small`: FLEURS 306 TSV files, 285,358,149 bytes.
- `hf-dataset-mixed`: FLEURS Icelandic and Oromo TSV/audio files, 12 files and 1,848,207,780 bytes.
- `civitai-large`: DreamShaper 8 file ID `93211`, 2,132,625,894 bytes with fixed SHA256.

## Examples

From the repository root:

```powershell
.\python_embed\python.exe app\tests\benchmarks\benchmark_huggingface.py `
  --workload hf-model-large --profile balanced --concurrency 1 `
  --run-number 1 --cache-mode cold --disk-type ssd `
  --network-label "wired-1g" --resolve-only I:\benchmarks\hfdm-v2

.\python_embed\python.exe app\tests\benchmarks\benchmark_civitai.py `
  --segments 4 --run-number 1 --cache-mode cold --disk-type ssd `
  --network-label "wired-1g" --output I:\benchmarks\results\civitai-s4-r1.json `
  I:\benchmarks\hfdm-v2\civitai-s4-r1
```

Use a distinct empty destination per cold run. Keep every JSON result, including failures.

## Confirmed matrix

1. HF Model: `balanced`, `maximum`, and `hdd`, concurrency 1; three cold runs per cell.
2. Dataset small: `balanced` with concurrency 1/2/4/8; select a worker count, then compare the three profiles.
3. Dataset mixed: compare all three profiles at the selected worker count.
4. Civitai: 1/2/4/8 segments; three cold runs per cell.
5. Keep HF warm-cache and Civitai interrupted-resume runs separate from cold throughput ranking.

Results include identity, bytes, TTFB, elapsed/average/peak rates, CPU time, peak RSS on Windows, Range/fallback state for Civitai, and stat-based reconciliation duration. `retry_count` remains `null` where the provider does not expose a trustworthy count.

Choose the lowest-resource setting with 100% success whose median throughput is at least 90% of the workload's fastest median. `maximum` replaces `balanced` only if Model and Dataset mixed workloads both improve by at least 15% without failures or abnormal resource use. HDD results apply only to rotating disks.
