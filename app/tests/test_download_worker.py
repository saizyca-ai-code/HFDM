import io
import json

from hfdm import download_worker


def test_worker_passes_dataset_repo_type(monkeypatch, capsys) -> None:
    captured = {}

    def fake_download(**kwargs):
        captured.update(kwargs)
        return "download/datasets/owner/repo/file.parquet"

    monkeypatch.setattr(download_worker, "hf_hub_download", fake_download)
    monkeypatch.setattr(
        download_worker.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "repo_id": "owner/repo",
                    "repo_type": "dataset",
                    "filename": "data/file.parquet",
                    "commit_hash": "a" * 40,
                    "destination": "download/datasets/owner/repo/commit",
                    "token": "hf_memory_only",
                }
            )
        ),
    )

    assert download_worker.main() == 0
    assert captured["repo_type"] == "dataset"
    assert captured["token"] == "hf_memory_only"
    assert json.loads(capsys.readouterr().out)["type"] == "complete"
