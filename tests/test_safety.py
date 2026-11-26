import json

import pytest

from src import pipeline
from src.core import safety

CANARY = "Zorvanth Kelmiqui"


@pytest.fixture
def input_dir(tmp_path):
    d = tmp_path / "input"
    (d / "notes").mkdir(parents=True)
    (d / "notes" / "a.txt").write_text("BP 120/80, Paracetamol 650 mg\n", encoding="utf-8")
    (d / "notes" / "b.txt").write_text(f"Name: {CANARY}\n", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "test-secret")


def _report(out):
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def _all_output_text(out):
    return "".join(p.read_text(encoding="utf-8", errors="ignore") for p in out.rglob("*") if p.is_file())


def test_log_prints_only_event_and_numbers(capsys):
    safety.log("done", files=3, name=CANARY, flag=True, seconds=1.5)
    assert capsys.readouterr().out == "done files=3 seconds=1.5\n"


def test_log_replaces_an_invalid_event(capsys):
    safety.log(f"failed on {CANARY}")
    assert capsys.readouterr().out == "invalid_event\n"


def test_exception_hook_prints_class_name_only(capsys):
    safety._hook(ValueError, ValueError(f"bad value {CANARY}"), None)
    assert capsys.readouterr().err == "fatal ValueError\n"


def test_crash_quarantines_only_that_file_and_leaks_nothing(input_dir, tmp_path, monkeypatch, capfd):
    real = pipeline.parse

    def crashing(in_rel, raw):
        if in_rel.endswith("b.txt"):
            raise ValueError(f"cannot parse {raw.decode()}")
        return real(in_rel, raw)

    monkeypatch.setattr(pipeline, "parse", crashing)
    out = tmp_path / "out"
    assert pipeline.run(input_dir, out) == 0

    printed = capfd.readouterr()
    assert CANARY not in printed.out + printed.err
    assert CANARY not in _all_output_text(out)

    statuses = sorted((f["status"], f.get("reason")) for f in _report(out)["files"])
    assert statuses == [("quarantined", "processing_error"), ("sanitized", None)]
    assert (out / "sanitized" / "notes" / "a.txt").is_file()
    assert not (out / "sanitized" / "notes" / "b.txt").exists()


def test_watchdog_quarantines_unprocessed_files_and_still_reports(input_dir, tmp_path, monkeypatch):
    policy = tmp_path / "policy.yaml"
    policy.write_text("time_budget_seconds: 0\n", encoding="utf-8")
    monkeypatch.setenv("UMBRELLA_POLICY", str(policy))
    out = tmp_path / "out"
    assert pipeline.run(input_dir, out) == 0

    report = _report(out)
    assert [f["reason"] for f in report["files"]] == ["time_budget", "time_budget"]
    assert report["counts"]["files_total"] == 2
    assert report["counts"]["files_quarantined"] == 2
    assert not any((out / "sanitized").rglob("*.txt"))


def test_missing_secret_stops_before_writing_anything(input_dir, tmp_path, monkeypatch, capfd):
    monkeypatch.delenv("RAKSHA_SECRET")
    out = tmp_path / "out"
    assert pipeline.run(input_dir, out) == 2
    assert not out.exists()
    assert capfd.readouterr().err == "error_secret_missing\n"


def test_watchdog_counts_from_its_own_start():
    now = [100.0]
    w = safety.Watchdog(10, clock=lambda: now[0])
    assert not w.expired()
    now[0] = 110.0
    assert w.expired()
