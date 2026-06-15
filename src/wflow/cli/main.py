"""WFlow CLI — thin HTTP client over FastAPI."""

import os
from pathlib import Path

import click

API_URL = os.environ.get("WFLOW_SERVER_URL", "http://localhost:8100")


def _handle_response(resp, action: str) -> dict:
    """Handle HTTP response, showing friendly error messages."""
    if resp.status_code == 404:
        detail = resp.json().get("detail", "Not found") if resp.text else "Not found"
        click.echo(f"Error: {detail}", err=True)
        raise SystemExit(1)
    if resp.status_code >= 400:
        detail = resp.json().get("detail", resp.text[:200]) if resp.text else f"HTTP {resp.status_code}"
        click.echo(f"Error ({resp.status_code}): {detail}", err=True)
        raise SystemExit(1)
    return resp.json() if resp.text else {}


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """WFlow — Claude Code CLI workflow orchestrator."""
    pass


@cli.group()
def server():
    """Server management commands."""
    pass


@server.command("start")
@click.option("--host", default="localhost", help="Bind address")
@click.option("--port", default=8100, type=int, help="Bind port")
@click.option("--db", default="./data/workflows.db", help="SQLite database path")
@click.option("--project-dir", default=None, help="Project root for .wflow directory detection")
def server_start(host, port, db, project_dir):
    """Start the WFlow server."""
    import uvicorn
    from pathlib import Path
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    click.echo(f"Starting WFlow server on {host}:{port}...")
    click.echo(f"Database: {db}")
    os.environ["WFLOW_DB_URL"] = f"sqlite+aiosqlite:///{Path(db).as_posix()}"
    if project_dir:
        os.environ["WFLOW_PROJECT_DIR"] = project_dir
        click.echo(f"Project dir: {project_dir}")
    uvicorn.run("wflow.main:create_app", host=host, port=port, factory=True)


@cli.group()
def workflow():
    """Workflow management commands."""
    pass


# Import and register the generate command (lives in its own module).
from wflow.cli.generate import generate
cli.add_command(generate)


@workflow.command("list")
@click.option("--status", default=None, help="Filter by status")
def workflow_list(status):
    """List all workflows."""
    import httpx
    params = {}
    if status:
        params["status"] = status
    resp = httpx.get(f"{API_URL}/api/v1/workflows", params=params)
    resp.raise_for_status()
    workflows = resp.json()
    if not workflows:
        click.echo("No workflows found.")
        return
    for w in workflows:
        click.echo(f"  {w['id'][:8]}  {w['name']:20s}  {w['status']:10s}  {w['created_at']}")


@workflow.command("show")
@click.argument("workflow_id")
def workflow_show(workflow_id):
    """Show workflow details."""
    import httpx, json
    resp = httpx.get(f"{API_URL}/api/v1/workflows/{workflow_id}")
    if resp.status_code == 404:
        click.echo(f"Workflow '{workflow_id}' not found.", err=True)
        return
    resp.raise_for_status()
    wf = resp.json()
    click.echo(f"Name: {wf['name']}")
    click.echo(f"Status: {wf['status']}")
    click.echo(f"Config:\n{json.dumps(wf['config'], indent=2, ensure_ascii=False)}")


@workflow.command("create")
@click.argument("file", type=click.Path(exists=True))
def workflow_create(file):
    """Create a workflow from a JSON file."""
    import httpx, json
    with open(file, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    payload = {
        "name": config_data.pop("name", Path(file).stem),
        "config": config_data,
    }
    resp = httpx.post(f"{API_URL}/api/v1/workflows", json=payload)
    resp.raise_for_status()
    wf = resp.json()
    click.echo(f"Created workflow: {wf['id']} ({wf['name']})")


@cli.group()
def run():
    """Run management commands."""
    pass


@run.command("start")
@click.argument("workflow_id")
@click.option("--input", "-i", "inputs", multiple=True, help="Input key=value (repeatable)")
@click.option("--watch", is_flag=True, help="Follow logs after starting")
def run_start(workflow_id, inputs, watch):
    """Start a workflow run."""
    import httpx, time
    input_dict = {}
    for inp in inputs:
        key, _, value = inp.partition("=")
        input_dict[key] = value

    resp = httpx.post(f"{API_URL}/api/v1/runs", json={
        "workflow_id": workflow_id, "inputs": input_dict,
    })
    run = _handle_response(resp, "start run")
    click.echo(f"Started run: {run['id']} (status: {run['status']})")

    if watch:
        click.echo("Watching logs (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(2)
                log_resp = httpx.get(f"{API_URL}/api/v1/runs/{run['id']}/logs?limit=5")
                for log in reversed(log_resp.json()):
                    click.echo(f"  [{log['level']}] {log['message']}")
                status_resp = httpx.get(f"{API_URL}/api/v1/runs/{run['id']}")
                status = status_resp.json()["status"]
                if status in ("completed", "failed"):
                    click.echo(f"Run {status}.")
                    break
        except KeyboardInterrupt:
            click.echo("\nStopped watching.")


@run.command("status")
@click.argument("run_id")
def run_status(run_id):
    """Check run status with node details."""
    import httpx, json
    resp = httpx.get(f"{API_URL}/api/v1/runs/{run_id}")
    if resp.status_code == 404:
        click.echo(f"Run '{run_id}' not found.", err=True)
        return
    resp.raise_for_status()
    r = resp.json()
    click.echo(f"Run:      {r['id']}")
    click.echo(f"Status:   {r['status']}")
    wd = r.get("work_dir", "")
    if wd:
        click.echo(f"Work Dir: {wd}")
    click.echo(f"Started:  {r.get('started_at', 'N/A')}")
    if r.get("finished_at"):
        click.echo(f"Finished: {r['finished_at']}")
    click.echo()

    # DAG overview
    spec = r.get("spec", {})
    spec_nodes = {n["id"]: n for n in spec.get("nodes", [])}
    edges = spec.get("edges", [])
    if edges:
        click.echo("Workflow DAG:")
        for e in edges:
            frm = e.get("from", "?")
            to = e.get("to", "end") or "end"
            cond = f"  [{e['condition']}]" if e.get("condition") else ""
            click.echo(f"  {frm} → {to}{cond}")
        click.echo()

    # Node executions
    click.echo("Node Executions:")
    icons = {"completed": "✓", "running": "◉", "failed": "✗", "pending": "○"}
    for n in r.get("nodes", []):
        icon = icons.get(n["status"], "?")
        sid = n.get("session_id", "")
        sid_str = f"  session: {sid[:8]}..." if sid else ""
        click.echo(f"  {icon} {n['node_id']} ({n['type']}) — {n['status']}{sid_str}")
        if n.get("retry_count", 0) > 0:
            click.echo(f"      retries: {n['retry_count']}")
        # Show input summary
        inp = n.get("input", "{}")
        if inp and inp != "{}":
            try:
                inp_data = json.loads(inp)
                click.echo(f"      input: {json.dumps(inp_data, ensure_ascii=False)[:120]}")
            except json.JSONDecodeError:
                click.echo(f"      input: {inp[:120]}")
        # Show output summary
        out = n.get("output", "")
        if out:
            try:
                out_data = json.loads(out)
                click.echo(f"      output: {json.dumps(out_data, ensure_ascii=False)[:200]}")
            except json.JSONDecodeError:
                click.echo(f"      output: {out[:200]}")
        if n.get("error"):
            click.echo(f"      error: {n['error'][:120]}")


@run.command("pause")
@click.argument("run_id")
def run_pause(run_id):
    """Pause a running workflow."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/runs/{run_id}/pause")
    resp.raise_for_status()
    click.echo(f"Run '{run_id}' paused.")


@run.command("resume")
@click.argument("run_id")
def run_resume(run_id):
    """Resume a paused workflow."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/runs/{run_id}/resume")
    resp.raise_for_status()
    click.echo(f"Run '{run_id}' resumed.")


@run.command("stop")
@click.argument("run_id")
def run_stop(run_id):
    """Stop a running workflow."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/runs/{run_id}/stop")
    resp.raise_for_status()
    click.echo(f"Run '{run_id}' stopped.")


@run.command("logs")
@click.argument("run_id")
@click.option("--follow", "-f", is_flag=True, help="Follow logs")
@click.option("--level", default=None, help="Filter by level")
def run_logs(run_id, follow, level):
    """View run logs."""
    import httpx, time
    if follow:
        click.echo("Following logs (Ctrl+C to stop)...")
        seen = set()
        try:
            while True:
                params = {"limit": 50}
                if level:
                    params["level"] = level
                resp = httpx.get(f"{API_URL}/api/v1/runs/{run_id}/logs", params=params)
                for log in reversed(resp.json()):
                    if log["id"] not in seen:
                        seen.add(log["id"])
                        click.echo(f"[{log['level']:5s}] {log['timestamp']} {log['message']}")
                time.sleep(2)
        except KeyboardInterrupt:
            click.echo("\nDone.")
    else:
        params = {"limit": 100}
        if level:
            params["level"] = level
        resp = httpx.get(f"{API_URL}/api/v1/runs/{run_id}/logs", params=params)
        for log in reversed(resp.json()):
            click.echo(f"[{log['level']:5s}] {log['message']}")


@cli.group()
def cron():
    """Cron job management commands."""
    pass


@cron.command("list")
def cron_list():
    """List cron jobs."""
    import httpx
    resp = httpx.get(f"{API_URL}/api/v1/cron")
    resp.raise_for_status()
    jobs = resp.json()
    if not jobs:
        click.echo("No cron jobs.")
        return
    for j in jobs:
        status_str = "enabled" if j["enabled"] else "disabled"
        click.echo(f"  {j['id'][:8]}  {j['workflow_id'][:8]}  {status_str}  {j['cron_expr']}")


@cron.command("add")
@click.argument("workflow_id")
@click.argument("cron_expr")
@click.option("--input", "-i", "inputs", multiple=True, help="Input key=value (repeatable)")
def cron_add(workflow_id, cron_expr, inputs):
    """Add a cron job."""
    import httpx, json
    input_dict = {}
    for inp in inputs:
        key, _, value = inp.partition("=")
        input_dict[key] = value
    resp = httpx.post(f"{API_URL}/api/v1/cron", json={
        "workflow_id": workflow_id, "cron_expr": cron_expr, "inputs": input_dict,
    })
    resp.raise_for_status()
    click.echo(f"Created cron job: {resp.json()['id']}")


@cron.command("remove")
@click.argument("cron_id")
def cron_remove(cron_id):
    """Remove a cron job."""
    import httpx
    resp = httpx.delete(f"{API_URL}/api/v1/cron/{cron_id}")
    resp.raise_for_status()
    click.echo(f"Cron job '{cron_id}' removed.")


@cron.command("toggle")
@click.argument("cron_id")
def cron_toggle(cron_id):
    """Enable/disable a cron job."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/cron/{cron_id}/toggle")
    resp.raise_for_status()
    status_str = "enabled" if resp.json()["enabled"] else "disabled"
    click.echo(f"Cron job '{cron_id}' {status_str}.")
