from click.testing import CliRunner
from wflow.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "workflow" in result.output
    assert "run" in result.output
    assert "cron" in result.output


def test_server_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    assert "start" in result.output


def test_workflow_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["workflow", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "show" in result.output
    assert "create" in result.output


def test_run_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "start" in result.output
    assert "status" in result.output


def test_cron_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["cron", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "add" in result.output
