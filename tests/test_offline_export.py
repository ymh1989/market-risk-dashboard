import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_FILE = ROOT / "scripts" / "export_offline_dashboard.py"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_offline_dashboard", EXPORTER_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_offline_export_is_single_file_and_interactive(tmp_path):
    exporter = load_exporter()
    output = tmp_path / "dashboard.html"

    written = exporter.write_snapshot(output, timestamped_copy=False)

    assert written == [output]
    assert output.stat().st_size > 1_000_000
    html = output.read_text(encoding="utf-8")
    assert '<meta name="offline-snapshot" content="true" />' in html
    assert '<html lang="ko" class="is-offline-snapshot">' in html
    assert "const OFFLINE_DATA = Object.freeze(" in html
    assert 'const OFFLINE_ADMIN_TAB_IDS = new Set(["operations", "model-monitoring"]);' in html
    assert "!OFFLINE_ADMIN_TAB_IDS.has(tab.id)" in html
    assert 'IS_OFFLINE_SNAPSHOT ? "" : renderOperationStatusStrip(pipelineStatus)' in html
    assert '.is-offline-snapshot [data-tab="operations"]' in html
    assert '.is-offline-snapshot [data-tab="model-monitoring"]' in html
    assert "오프라인 스냅샷" in html
    assert "--desktop-ui-scale: 0.85;" in html
    assert "zoom: var(--desktop-ui-scale);" in html
    assert "function effectiveChartZoom" in html
    assert "tooltipPointerX = (event.clientX - chartRect.left) / cssZoom" in html
    assert "function updateChartCursor" in html
    assert "function marketTrendRangeMetric" in html
    assert "<dt>${label} 변동</dt>" in html
    assert 'chart.addEventListener("pointermove"' in html
    assert "function activateChartRange" in html
    assert "data-observation-detail-toggle" in html
    assert "data-source-detail-toggle" in html
    assert "function renderModelMonitoringPage" in html
    assert '"data/market-risk-snapshot.json"' in html
    assert '"data/data-quality.json"' in html
    assert 'src="./src/app.js' not in html
    assert 'href="./src/styles.css' not in html
    assert "fetch(versioned(path)" not in html
    assert "images.unsplash.com" not in html


def test_offline_export_uses_dashboard_timestamp(tmp_path):
    exporter = load_exporter()
    output = tmp_path / "market-risk-dashboard-offline.html"

    written = exporter.write_snapshot(output, timestamped_copy=True)

    assert len(written) == 2
    assert written[1].name.startswith("market-risk-dashboard-20")
    assert written[1].name.endswith("-KST.html")
    assert written[0].read_bytes() == written[1].read_bytes()


def test_homepage_and_update_pipelines_publish_latest_snapshot():
    app_source = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "src" / "styles.css").read_text(encoding="utf-8")
    local_update = (ROOT / "scripts" / "run_local_market_update.sh").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "update-market-risk.yml").read_text(
        encoding="utf-8"
    )

    assert "function offlineSnapshotFilename" in app_source
    assert 'class="hero__download"' in app_source
    assert 'href="./reports/market-risk-dashboard-offline.html' in app_source
    assert 'download="${offlineSnapshotFilename(data)}"' in app_source
    assert ".hero__download" in styles
    assert "scripts/export_offline_dashboard.py --stable-only" in local_update
    assert "reports/market-risk-dashboard-offline.html" in local_update
    assert "scripts/export_offline_dashboard.py --stable-only" in workflow
    assert "reports/market-risk-dashboard-offline.html" in workflow
