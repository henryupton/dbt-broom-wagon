import json

import click
from click.testing import CliRunner

from broom_wagon.cli import main
from tests.factory import manifest, node, run_results, write_run

A, B = "model.proj.a", "model.proj.b"


def setup(tmp_path, *, modified=True):
    runs = tmp_path / "runs"
    write_run(
        runs, 1, manifest(node(A, checksum="v1"), node(B, depends_on=[A])), run_results({A: "success", B: "success"})
    )
    target = tmp_path / "manifest.json"
    checksum = "v2" if modified else "v1"
    target.write_text(json.dumps(manifest(node(A, checksum=checksum), node(B, depends_on=[A]))))
    return ["select", "--source", "local", "--runs-dir", str(runs), "--manifest", str(target)]


def test_prints_the_select_string(tmp_path):
    result = CliRunner().invoke(main, setup(tmp_path))
    assert result.exit_code == 0, result.output
    assert result.stdout == "a+\n"
    assert "broom-wagon: 2 nodes (1 roots) over 1 runs" in result.stderr
    assert "modified=1" in result.stderr


def test_json_to_file_and_github_output(tmp_path, monkeypatch):
    gh = tmp_path / "gh.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    out = tmp_path / "out.json"
    result = CliRunner().invoke(main, [*setup(tmp_path), "--format", "json", "--out", str(out), "--github-output"])
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["count"] == 2
    assert payload["select"] == "a+"
    assert gh.read_text() == "select=a+\ncount=2\n"


def test_empty_selection_exits_zero(tmp_path, monkeypatch):
    gh = tmp_path / "gh.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    result = CliRunner().invoke(main, [*setup(tmp_path, modified=False), "--github-output"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert gh.read_text() == "select=\ncount=0\n"


def test_no_history_fails(tmp_path):
    (tmp_path / "runs").mkdir()
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest(node(A))))
    args = ["select", "--source", "local", "--runs-dir", str(tmp_path / "runs"), "--manifest", str(target)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1
    assert "no runs with build steps" in result.stderr


def test_unreadable_manifest_fails(tmp_path):
    args = setup(tmp_path)
    (tmp_path / "manifest.json").write_text("not json")
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1


def test_dbt_cloud_needs_ids(tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest()))
    result = CliRunner().invoke(main, ["select", "--source", "dbt-cloud", "--manifest", str(target)])
    assert result.exit_code == 2


def test_github_output_needs_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    result = CliRunner().invoke(main, [*setup(tmp_path), "--github-output"])
    assert result.exit_code == 1
    assert "GITHUB_OUTPUT" in result.stderr


def test_github_output_precondition_fails_before_any_output(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    out = tmp_path / "out.txt"
    result = CliRunner().invoke(main, [*setup(tmp_path), "--out", str(out), "--github-output"])
    assert result.exit_code == 1
    assert "GITHUB_OUTPUT" in result.output
    assert not out.exists()


def test_host_is_normalised_before_building_the_source(tmp_path, monkeypatch):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        raise click.ClickException("stop")

    monkeypatch.setattr("broom_wagon.cli.DbtCloudSource", fake)
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text("{}")
    args = ["select", "--source", "dbt-cloud", "--account-id", "1", "--job-id", "2", "--api-token", "t"]
    CliRunner().invoke(main, [*args, "--host", "https://au1.dbt.com/", "--manifest", str(manifest_path)])
    assert seen["host"] == "au1.dbt.com"
