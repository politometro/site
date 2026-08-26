from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_launcher_runs_unlimited_local_collection_without_pinecone_upload():
    launcher = (ROOT / "executar_recolha.cmd").read_text(encoding="utf-8")

    assert "scripts\\political_intelligence.py all" in launcher
    assert "--since-days 0" in launcher
    assert "--all-history" in launcher
    assert "--force-assembly" in launcher
    assert "--max-detail-pages all" in launcher
    assert "--max-urls-per-source" not in launcher
    assert "upload_political_intelligence.py" not in launcher


def test_automatic_workflow_has_no_collection_timeout_or_pinecone_upload():
    workflow = (
        ROOT / ".github" / "workflows" / "sync_political_intelligence.yml"
    ).read_text(encoding="utf-8")

    assert "timeout-minutes" not in workflow
    assert "--max-urls-per-source" not in workflow
    assert "upload_political_intelligence.py" not in workflow
    assert "pinecone" not in workflow.casefold()
