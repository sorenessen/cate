import json
from pathlib import Path

from cate.logging_utils import write_evidence_manifest


def _touch(p: Path, content: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_write_evidence_manifest_collects_siblings(tmp_path: Path) -> None:
    prefix = tmp_path / "runs" / "demo"

    # create sibling artifacts
    _touch(prefix.with_suffix(".jsonl"), '{"ok":true}\n')
    _touch(prefix.with_suffix(".summary.json"), '{"kind":"http-flow"}')
    _touch(prefix.with_suffix(".summary.md"), "# summary\n")
    _touch(prefix.with_suffix(".report.html"), "<html></html>")
    _touch(prefix.with_suffix(".report.md"), "# report\n")
    _touch(prefix.with_suffix(".signals.json"), '{"severity":"none"}')
    _touch(prefix.with_suffix(".signals.md"), "# signals\n")
    _touch(Path(str(prefix) + ".exit.pass.png"), "pngdata")

    manifest_path = write_evidence_manifest(
        output_prefix=str(prefix),
        kind="http-flow",
        env="dev",
        command="cate http-flow ...",
        version="0.5.0",
        extra={"flow": "demo"},
    )

    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert m["schema_version"] == 1
    assert m["run"]["kind"] == "http-flow"
    assert m["tool"]["name"] == "cate"
    assert m["tool"]["version"] == "0.5.0"

    arts = m["artifacts"]
    assert "results_jsonl" in arts
    assert "summary_json" in arts
    assert "report_html" in arts
    assert "signals_json" in arts
    assert "exit_snapshots_png" in arts
    assert isinstance(arts["exit_snapshots_png"], list)
    assert arts["exit_snapshots_png"][0]["name"].endswith(".exit.pass.png")
