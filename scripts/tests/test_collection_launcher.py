from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_launcher_runs_unlimited_local_collection_and_incremental_pinecone_upload():
    launcher = (ROOT / "executar_recolha.cmd").read_text(encoding="utf-8")

    assert "scripts\\political_intelligence.py all" in launcher
    assert "--since-days 0" in launcher
    assert "--all-history" in launcher
    assert "--force-assembly" in launcher
    assert "--max-detail-pages all" in launcher
    assert "--max-urls-per-source" not in launcher
    assert "extract_eu_budget.py" in launcher
    assert "upload_pinecone.py --embedding-mode local" in launcher


def test_automatic_workflow_runs_weekly_incremental_pinecone_upload():
    workflow = (
        ROOT / ".github" / "workflows" / "sync_political_intelligence.yml"
    ).read_text(encoding="utf-8")

    assert "cron: '0 11 * * 6'" in workflow
    assert "cron: '0 10 * * 6'" in workflow
    assert "Europe/Lisbon" in workflow
    assert "timeout-minutes: 360" in workflow
    assert "--max-urls-per-source" not in workflow
    assert "upload_political_intelligence.py" not in workflow
    assert "upload_pinecone.py --embedding-mode local" in workflow
