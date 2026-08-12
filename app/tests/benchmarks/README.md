# HFDM V2 transfer benchmark design

Phase 0 defines the workloads and measurement contract; provider implementations and live benchmark execution belong to their later phases.

## Workloads

1. Hugging Face Model large-file workload: one public immutable revision containing a multi-gigabyte LFS/Xet file. Compare balanced, maximum, and HDD profiles.
2. Hugging Face Dataset many-file workload: one public immutable revision with at least 1,000 mixed small files and representative larger shards. Measure metadata, queue, download, and reconciliation overhead separately.
3. Civitai large-file workload: one stable public model-version file. Compare 1/2/4/8 HTTP ranges only after the Phase 3 provider exists; Phase 0 does not call or implement Civitai.

## Recorded fields

- provider, repo type, immutable revision/version and selected file count;
- expected and transferred bytes, time to first byte, elapsed time;
- average and peak bytes per second, retry count and terminal result;
- CPU, peak process memory, disk type and reconciliation duration;
- profile, concurrency/segment count and whether a fallback occurred.

Run each cold and warm-cache case at least three times. Do not infer Dataset defaults from the Model workload or publish tokens, signed URLs, or private repository identifiers.
