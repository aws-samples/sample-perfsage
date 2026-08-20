"""CLI interface for the PerfSage Executor Agent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from perfsage_executor.models.test_config import ExecutionMode, TestConfig, ThresholdConfig

console = Console()

# executor_agent/ is 2 levels up from this file; repo root is one more level up
EXECUTOR_ROOT = Path(__file__).parent.parent.parent
REPO_ROOT = EXECUTOR_ROOT.parent


@click.group()
@click.version_option(version="0.1.0", prog_name="perfsage-executor")
def main() -> None:
    """PerfSage Executor Agent — Run k6 load tests with AI-powered orchestration.

    \b
    Quick Start:
      1. perfsage-executor init        # Verify prerequisites
      2. perfsage-executor deploy       # Deploy AWS infrastructure
      3. perfsage-executor run ...      # Run a load test
    """
    pass


# ─── Init Command ───────────────────────────────────────────────────────────────


@main.command()
def init() -> None:
    """Verify prerequisites and AWS credentials. Run this first after cloning."""
    console.print(Panel("[bold blue]PerfSage Executor — Setup Check[/bold blue]"))
    console.print()

    all_good = True

    # Check tools
    tools = {
        "python3": "Python 3.11+ runtime",
        "docker": "Docker (for local mode + image builds)",
        "aws": "AWS CLI (configure with: aws configure)",
        "cdk": "AWS CDK CLI (install: npm install -g aws-cdk)",
    }

    table = Table(title="Prerequisites")
    table.add_column("Tool", style="cyan")
    table.add_column("Status")
    table.add_column("Purpose")

    for tool, purpose in tools.items():
        found = _check_command(tool)
        status = "[green]✓ installed[/green]" if found else "[red]✗ missing[/red]"
        if not found:
            all_good = False
        table.add_row(tool, status, purpose)

    # Check Docker running
    docker_running = _check_command("docker") and _run_silent("docker info")
    table.add_row(
        "docker (running)",
        "[green]✓ running[/green]" if docker_running else "[yellow]⚠ not running[/yellow]",
        "Required for building images",
    )

    console.print(table)
    console.print()

    # Check AWS credentials
    console.print("[bold]AWS Credentials:[/bold]")
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            identity = json.loads(result.stdout)
            console.print(f"  Account:  [green]{identity['Account']}[/green]")
            console.print(f"  ARN:      [green]{identity['Arn']}[/green]")
            console.print(f"  Region:   [green]{os.environ.get('AWS_REGION', 'us-east-1')}[/green]")
        else:
            all_good = False
            console.print("  [red]✗ Not configured[/red]")
            console.print("  Run: [cyan]aws configure[/cyan]")
    except Exception:
        all_good = False
        console.print("  [red]✗ AWS CLI not available or credentials not set[/red]")

    console.print()

    # Check if already deployed
    env_file = REPO_ROOT / ".env"
    cdk_outputs = REPO_ROOT / ".cdk-outputs.json"
    if env_file.exists() or cdk_outputs.exists():
        console.print("[bold]Deployment Status:[/bold]  [green]✓ Infrastructure deployed[/green]")
        console.print(f"  Config: {env_file}")
    else:
        console.print("[bold]Deployment Status:[/bold]  [yellow]⚠ Not deployed yet[/yellow]")
        console.print("  Run: [cyan]perfsage-executor deploy[/cyan]")

    console.print()

    if all_good:
        console.print("[green]✓ All checks passed! You're ready to go.[/green]")
        if not (env_file.exists() or cdk_outputs.exists()):
            console.print("  Next step: [cyan]perfsage-executor deploy[/cyan]")
    else:
        console.print("[yellow]⚠ Some issues found. Fix them before deploying.[/yellow]")


# ─── Deploy Command ─────────────────────────────────────────────────────────────


@main.command()
@click.option("--region", default=None, help="AWS region (default: from AWS config)")
def deploy(region: str | None) -> None:
    """Deploy PerfSage infrastructure to AWS. Checks prerequisites first, then deploys everything.

    \b
    This single command:
      1. Verifies all prerequisites (Python, Docker, AWS CLI, CDK)
      2. Validates AWS credentials
      3. Creates ECR repository and pushes k6 image
      4. Deploys all CDK stacks (S3, DynamoDB, VPC, ECS, WebSocket API)
      5. Auto-generates .env with all resource IDs

    After this, you can run tests immediately with:
      perfsage-executor run -s your_test.js --vus 100 --duration 5m
    """
    console.print(Panel("[bold blue]PerfSage Executor — Deploy[/bold blue]"))
    console.print()

    # ─── Pre-flight checks (same as init, but blocks on failure) ────────────────
    console.print("[bold]Pre-flight checks:[/bold]")

    # Check tools
    required_tools = {
        "python3": "Python 3.11+",
        "docker": "Docker",
        "aws": "AWS CLI",
        "cdk": "AWS CDK CLI",
    }
    missing = []
    for tool, label in required_tools.items():
        if not _check_command(tool):
            missing.append((tool, label))
            console.print(f"  [red]✗ {label} not found[/red]")
        else:
            console.print(f"  [green]✓ {label}[/green]")

    if missing:
        console.print(f"\n[red]Missing tools. Install them first:[/red]")
        for tool, label in missing:
            if tool == "cdk":
                console.print(f"  npm install -g aws-cdk")
            elif tool == "aws":
                console.print(f"  https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html")
        sys.exit(1)

    # Check Docker running
    if not _run_silent("docker info"):
        console.print(f"  [red]✗ Docker is not running[/red]")
        console.print(f"  Start Docker Desktop or run: [cyan]colima start[/cyan]")
        sys.exit(1)
    console.print(f"  [green]✓ Docker running[/green]")

    # Check AWS credentials
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            console.print(f"  [red]✗ AWS credentials not configured[/red]")
            console.print(f"  Run: [cyan]aws configure[/cyan]")
            sys.exit(1)
        identity = json.loads(result.stdout)
        console.print(f"  [green]✓ AWS Account: {identity['Account']}[/green]")
    except Exception:
        console.print(f"  [red]✗ Cannot verify AWS credentials[/red]")
        sys.exit(1)

    console.print()
    console.print("[green]All checks passed.[/green] Starting deployment...\n")

    # ─── Run deploy script ──────────────────────────────────────────────────────
    deploy_script = REPO_ROOT / "scripts" / "deploy.sh"
    if not deploy_script.exists():
        console.print("[red]Deploy script not found. Are you in the project root?[/red]")
        sys.exit(1)

    env = os.environ.copy()
    if region:
        env["AWS_REGION"] = region

    try:
        process = subprocess.run(
            ["bash", str(deploy_script)],
            env=env,
            cwd=str(REPO_ROOT),
        )
        if process.returncode != 0:
            console.print("\n[red]Deployment failed. Check output above for details.[/red]")
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[red]Deployment cancelled.[/red]")
        sys.exit(1)


# ─── Destroy Command ────────────────────────────────────────────────────────────


@main.command()
@click.confirmation_option(prompt="This will destroy all PerfSage AWS infrastructure (3 CloudFormation stacks). Continue?")
def destroy() -> None:
    """Tear down all PerfSage AWS infrastructure via CloudFormation."""
    console.print("[yellow]Destroying all PerfSage CloudFormation stacks...[/yellow]\n")

    infra_dir = REPO_ROOT / "infra"
    if not infra_dir.exists():
        console.print("[red]Infra directory not found.[/red]")
        sys.exit(1)

    try:
        process = subprocess.run(
            ["cdk", "destroy", "--all", "--force"],
            cwd=str(infra_dir),
        )
        if process.returncode == 0:
            # Clean up local files
            env_file = REPO_ROOT / ".env"
            cdk_outputs = REPO_ROOT / ".cdk-outputs.json"
            if env_file.exists():
                env_file.unlink()
            if cdk_outputs.exists():
                cdk_outputs.unlink()
            console.print("\n[green]✓ All infrastructure destroyed.[/green]")
        else:
            console.print("\n[red]Destroy failed. Check CloudFormation console.[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ─── Run Command ────────────────────────────────────────────────────────────────


@main.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Path to test config JSON/YAML file")
@click.option("--script", "-s", type=click.Path(exists=True), required=True, help="Path to k6 test script")
@click.option("--mode", "-m", type=click.Choice(["local", "fargate"]), default=None, help="Execution mode (auto-detected from .env if not set)")
@click.option("--vus", type=int, default=10, help="Number of virtual users")
@click.option("--duration", "-d", type=str, default="30s", help="Test duration (e.g., 5m, 30s)")
@click.option("--ramp-up", type=str, default="0s", help="Ramp-up period (e.g., 2m)")
@click.option("--p99-threshold", type=float, default=None, help="Max acceptable p99 latency (ms)")
@click.option("--error-threshold", type=float, default=None, help="Max acceptable error rate (%)")
@click.option("--auto-stop/--no-auto-stop", default=True, help="Auto-terminate on critical anomaly")
@click.option("--target-url", type=str, default=None, help="Target API base URL")
def run(
    config: str | None,
    script: str,
    mode: str | None,
    vus: int,
    duration: str,
    ramp_up: str,
    p99_threshold: float | None,
    error_threshold: float | None,
    auto_stop: bool,
    target_url: str | None,
) -> None:
    """Run a k6 load test with the Executor Agent.

    \b
    Examples:
      # Local (no AWS needed):
      perfsage-executor run -s test.js --vus 50 --duration 5m

      # AWS Fargate (after deploy):
      perfsage-executor run -s test.js --mode fargate --vus 500 --duration 10m

      # With config file:
      perfsage-executor run -c config.json -s test.js
    """
    from perfsage_executor.config import get_settings

    settings = get_settings()

    console.print(Panel("[bold blue]PerfSage Executor Agent[/bold blue]", subtitle="Starting load test"))

    # Auto-detect mode from settings if not specified
    if mode is None:
        mode = settings.agent.execution_mode
        console.print(f"  Mode auto-detected: [cyan]{mode}[/cyan] (from .env)")

    # Validate fargate mode has required config
    if mode == "fargate" and not settings.ecs.subnets:
        console.print("[red]Fargate mode requires subnet configuration.[/red]")
        console.print("Run [cyan]perfsage-executor deploy[/cyan] first, or use [cyan]--mode local[/cyan]")
        sys.exit(1)

    # Build test config
    if config:
        config_path = Path(config)
        config_data = json.loads(config_path.read_text())
        test_config = TestConfig.model_validate(config_data)
        # Allow CLI overrides
        if mode:
            test_config.execution_mode = ExecutionMode(mode)
    else:
        from perfsage_executor.models.test_config import TestScenarioParams

        scenario = None
        if target_url:
            scenario = TestScenarioParams(target_url=target_url)

        test_config = TestConfig(
            script_path=str(Path(script).resolve()),
            virtual_users=vus,
            duration=duration,
            ramp_up=ramp_up,
            execution_mode=ExecutionMode(mode),
            thresholds=ThresholdConfig(
                p99_latency_ms=p99_threshold,
                error_rate_pct=error_threshold,
            ),
            auto_stop_on_anomaly=auto_stop,
            scenario_params=scenario,
        )

    # Display config summary
    _display_config(test_config)

    # Run the agent
    console.print("\n[yellow]Starting test execution...[/yellow]\n")

    try:
        from perfsage_executor.agent import run_test

        result = run_test(test_config.model_dump_json())
        console.print(Panel(result, title="[green]Test Complete[/green]"))
    except KeyboardInterrupt:
        console.print("\n[red]Test aborted by user[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)


# ─── Status Command ─────────────────────────────────────────────────────────────


@main.command()
@click.option("--test-id", required=True, help="Test run ID to check")
def status(test_id: str) -> None:
    """Check the status of a running or completed test."""
    from perfsage_executor.services.dynamodb_service import DynamoDBService

    db_svc = DynamoDBService()
    item = db_svc.get_test_run(test_id)

    if not item:
        console.print(f"[red]Test run not found: {test_id}[/red]")
        return

    table = Table(title=f"Test Run: {test_id}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("Status", item.get("status", "unknown"))
    table.add_row("Started", str(item.get("started_at", "")))
    table.add_row("Ended", str(item.get("ended_at", "")))
    table.add_row("Metrics", item.get("metrics_location", ""))

    if "summary" in item:
        summary = item["summary"]
        table.add_row("Total Requests", str(summary.get("total_requests", "")))
        table.add_row("Error Rate", f"{summary.get('error_rate_pct', 0):.2f}%")
        table.add_row("p99 Latency", f"{summary.get('p99_latency_ms', 0):.1f} ms")
        table.add_row("Avg RPS", f"{summary.get('avg_rps', 0):.1f}")

    console.print(table)


# ─── Abort Command ──────────────────────────────────────────────────────────────


@main.command()
@click.option("--test-id", required=True, help="Test run ID to abort")
def abort(test_id: str) -> None:
    """Abort a running test."""
    from perfsage_executor.tools.execute_test import get_active_execution

    execution = get_active_execution(test_id)
    if not execution:
        console.print(f"[red]No active execution found for: {test_id}[/red]")
        return

    console.print(f"[yellow]Aborting test: {test_id}...[/yellow]")

    from perfsage_executor.tools.terminate_test import terminate_test

    infra = execution["infrastructure"]
    result = terminate_test(test_id, infra.model_dump_json(), reason="user_abort")
    result_data = json.loads(result)

    if result_data.get("status") == "terminated":
        console.print(f"[green]Test {test_id} aborted successfully[/green]")
    else:
        console.print(f"[red]Abort failed: {result_data.get('error', 'unknown')}[/red]")


# ─── List Command ───────────────────────────────────────────────────────────────


@main.command(name="list")
@click.option("--limit", "-n", type=int, default=10, help="Number of recent runs to show")
@click.option("--status-filter", type=str, default=None, help="Filter by status")
def list_runs(limit: int, status_filter: str | None) -> None:
    """List recent test runs."""
    from perfsage_executor.services.dynamodb_service import DynamoDBService

    db_svc = DynamoDBService()
    runs = db_svc.list_test_runs(limit=limit, status_filter=status_filter)

    if not runs:
        console.print("[yellow]No test runs found[/yellow]")
        return

    table = Table(title="Recent Test Runs")
    table.add_column("Test ID", style="cyan")
    table.add_column("Status")
    table.add_column("Started")
    table.add_column("Duration")

    for run_item in runs:
        status_val = run_item.get("status", "unknown")
        style = "green" if status_val == "completed" else "red" if status_val == "failed" else "yellow"
        table.add_row(
            run_item.get("test_id", ""),
            f"[{style}]{status_val}[/{style}]",
            str(run_item.get("started_at", "")),
            str(run_item.get("ended_at", "")),
        )

    console.print(table)


# ─── Config Command ─────────────────────────────────────────────────────────────


@main.command(name="config")
def show_config() -> None:
    """Show current configuration (auto-discovered + env vars)."""
    from perfsage_executor.config import get_settings

    settings = get_settings()

    table = Table(title="Active Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    table.add_column("Source")

    def source_of(env_key: str) -> str:
        if os.getenv(env_key):
            return "env/.env"
        return "auto-discovered" if settings.ecs.subnets else "default"

    table.add_row("Region", settings.aws.region, source_of("AWS_REGION"))
    table.add_row("Account ID", settings.aws.account_id or "(not set)", source_of("AWS_ACCOUNT_ID"))
    table.add_row("Execution Mode", settings.agent.execution_mode, source_of("PERFSAGE_EXECUTION_MODE"))
    table.add_row("S3 Bucket", settings.s3.bucket, source_of("PERFSAGE_S3_BUCKET"))
    table.add_row("DynamoDB Table", settings.dynamodb.table_name, source_of("PERFSAGE_DYNAMODB_TABLE"))
    table.add_row("ECS Cluster", settings.ecs.cluster, source_of("PERFSAGE_ECS_CLUSTER"))
    table.add_row("Subnets", ", ".join(settings.ecs.subnets) or "(none)", source_of("PERFSAGE_FARGATE_SUBNETS"))
    table.add_row("Security Groups", ", ".join(settings.ecs.security_groups) or "(none)", source_of("PERFSAGE_FARGATE_SECURITY_GROUPS"))
    table.add_row("WebSocket URL", settings.websocket.api_url or "(not set)", source_of("PERFSAGE_WEBSOCKET_API_URL"))
    table.add_row("Model", settings.agent.model_id, source_of("PERFSAGE_MODEL_ID"))

    console.print(table)


# ─── Helpers ────────────────────────────────────────────────────────────────────


def _check_command(cmd: str) -> bool:
    """Check if a command is available on PATH or common locations."""
    import shutil

    # First try shutil.which (most reliable, checks full PATH)
    if shutil.which(cmd):
        return True

    # Fallback: try running it directly
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check common macOS/Linux locations for aws, docker, cdk
    common_paths = [
        f"/usr/local/bin/{cmd}",
        f"/opt/homebrew/bin/{cmd}",
        f"/usr/bin/{cmd}",
        os.path.expanduser(f"~/.local/bin/{cmd}"),
    ]
    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return True

    return False


def _run_silent(cmd: str) -> bool:
    """Run a command silently and return whether it succeeded."""
    try:
        result = subprocess.run(cmd.split(), capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def _display_config(config: TestConfig) -> None:
    """Display test configuration in a formatted table."""
    table = Table(title="Test Configuration")
    table.add_column("Parameter", style="cyan")
    table.add_column("Value")

    table.add_row("Test ID", config.test_id)
    table.add_row("Script", config.script_path)
    table.add_row("Mode", config.execution_mode.value)
    table.add_row("Virtual Users", str(config.virtual_users))
    table.add_row("Duration", config.duration)
    table.add_row("Ramp-up", config.ramp_up)
    table.add_row("Auto-stop", "✓" if config.auto_stop_on_anomaly else "✗")

    if config.total_records:
        table.add_row("Total Records", str(config.total_records))
    if config.thresholds.p99_latency_ms:
        table.add_row("p99 Threshold", f"{config.thresholds.p99_latency_ms} ms")
    if config.thresholds.error_rate_pct:
        table.add_row("Error Threshold", f"{config.thresholds.error_rate_pct}%")
    if config.scenario_params and config.scenario_params.target_url:
        table.add_row("Target URL", config.scenario_params.target_url)

    console.print(table)


if __name__ == "__main__":
    main()
