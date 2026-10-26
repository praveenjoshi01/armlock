import sys
from pathlib import Path
from typing import Optional
import typer
import yaml
from rich.console import Console
from rich.table import Table

from common.exceptions import BudgetExceededError, MockDetectedError, ModelUnavailableError, PrerequisiteError, StaleUngatedRunError
from common.state import PipelineStateManager, StageStatus
from harness.runner import HarnessRunner

app = typer.Typer(name="sg", help="Robot Safety Gate CLI")
harness_app = typer.Typer(name="harness", help="Robot Harness commands")
data_app = typer.Typer(name="data", help="Dataset commands")
app.add_typer(harness_app, name="harness")
app.add_typer(data_app, name="data")

console = Console()


def load_config(config_path: str = "config.yaml") -> dict:
    p = Path(config_path)
    if not p.exists():
        console.print(f"[red]Error: {config_path} not found.[/red]")
        sys.exit(1)
    return yaml.safe_load(p.read_text())


def resolve_run_id(cfg: dict, run_id: Optional[str]) -> str:
    return run_id or cfg.get("project", {}).get("run_id", "run_002")


def fail(e: Exception) -> None:
    console.print(f"[bold red]{type(e).__name__}:[/bold red] {e}")
    sys.exit(1)


@harness_app.command("run")
def harness_run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Run scripted dry-run validation on all 10 scenes"),
    replication: bool = typer.Option(False, "--replication", help="Run ungated RoboHarm replication with real local models"),
    gated: bool = typer.Option(False, "--gated", help="Run gated harness trials (locked until Modules B and C pass)"),
    trials: Optional[int] = typer.Option(None, "--trials", "-n", help="Trials per condition"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID for results"),
    config_path: str = typer.Option("config.yaml", "--config", help="Config YAML file path")
):
    """Run robot harness commands (dry-run, replication, or gated trials)."""
    cfg = load_config(config_path)
    runner = HarnessRunner(config=cfg, run_id=resolve_run_id(cfg, run_id))

    if gated:
        try:
            runner.run_gated(trials_per_condition=trials)
        except (PrerequisiteError, ModelUnavailableError, MockDetectedError) as e:
            fail(e)
        return

    if dry_run or not replication:
        console.print("[cyan]Executing Module A dry-run validation across all 10 scenes...[/cyan]")
        res = runner.run_dry_run_all()
        table = Table(title="Module A Dry-Run Validation Results")
        for col in ("Scenario", "Variant", "Steps", "Full script", "Grasp only", "No action", "Tells", "Passed"):
            table.add_column(col)
        for r in res["results"]:
            by = {c["plan"]: c["got"] for c in r["checks"]}
            table.add_row(r["scenario_id"], r["variant"], str(r["steps_count"]), by["full_script"], by["grasp_only"],
                          by["no_action"], str(len(r["tells"])), "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]")
        console.print(table)
        if not res["all_passed"]:
            console.print("[bold red]Dry-run validation failed.[/bold red]")
            sys.exit(1)
        console.print("[bold green]Dry-run passed; the ungated replication run is unlocked.[/bold green]")

    if replication:
        try:
            res = runner.run_replication(trials_per_condition=trials, on_trial_callback=lambda u: console.print(
                f"[dim]{u['completed']}/{u['total']} {u['current_trial']['scenario_id']} "
                f"{u['current_trial']['variant']}/{u['current_trial']['framing']} -> {u['current_trial']['final_score']} "
                f"(ETA {u['eta_sec'] / 3600:.1f} h)[/dim]"))
        except (PrerequisiteError, ModelUnavailableError, MockDetectedError) as e:
            fail(e)
        console.print(f"[bold green]Replication complete: {res['total_trials']} trials.[/bold green] Report: {res['report_html']}")


@harness_app.command("rescore")
def harness_rescore(run_id: Optional[str] = typer.Option(None, "--run-id"), config_path: str = typer.Option("config.yaml", "--config")):
    """Replay a run's ungated and gated trials under the physics harness and write rescore_report."""
    from harness.rescore import rescore_run, write_rescore_report
    from harness.runner import load_all_scenarios
    run_dir = Path("results") / resolve_run_id(load_config(config_path), run_id)
    res = rescore_run(run_dir, load_all_scenarios())
    md, html = write_rescore_report(run_dir, res)
    console.print(f"Re-score report: {html}")


from data.runner import DatasetPipelineRunner


def _data_runner(run_id: Optional[str], config_path: str) -> DatasetPipelineRunner:
    cfg = load_config(config_path)
    return DatasetPipelineRunner(config=cfg, run_id=resolve_run_id(cfg, run_id))


@data_app.command("generate")
def data_generate(
    pairs: Optional[int] = typer.Option(None, "--target-pairs", "--pairs", "-n", help="Pairs to generate (default: config target_pairs_generate)"),
    workers: Optional[int] = typer.Option(None, "--workers", help="Concurrent Gemma requests"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Seed + RLCD generation with local Gemma (resumable). Stops at the target and reports throughput."""
    runner = _data_runner(run_id, config_path)
    try:
        runner.state_mgr.check_prerequisites_for_stage("B_dataset")
        stats = runner.generate(pairs, n_workers=workers, on_progress=lambda p: console.print(
            f"[dim]{p['pairs_done']}/{p['target_pairs']} pairs, {p['failures']} failed, ETA {p['eta_sec'] / 3600:.1f} h[/dim]"))
    except (PrerequisiteError, ModelUnavailableError) as e:
        fail(e)
    console.print(f"[bold green]Generated {stats['pairs']} pairs[/bold green] at {stats['tokens_per_sec']} tok/s, "
                  f"{stats['sec_per_pair']} s/pair. Projected: 5k pairs = {stats['projected_hours_5k_pairs']} h, "
                  f"10k = {stats['projected_hours_10k_pairs']} h. Review the projection before generating more.")


@data_app.command("review")
def data_review(
    sample: Optional[int] = typer.Option(None, "--sample", help="Records to review (default: config review_sample_size)"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Blind OpenAI review of a stratified sample (needs a real OPENAI_API_KEY in .env; budget-capped)."""
    runner = _data_runner(run_id, config_path)
    try:
        s = runner.review(sample)
    except (ModelUnavailableError, MockDetectedError, BudgetExceededError) as e:
        fail(e)
    console.print(f"[bold green]Reviewed {s['sample_size']} records[/bold green]: agreement {s['agreement_rate'] * 100:.1f}% "
                  f"(CI {s['agreement_ci95'][0] * 100:.1f}-{s['agreement_ci95'][1] * 100:.1f}%), spend ${s['spend_usd']:.4f}")


@data_app.command("split")
def data_split(
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Apply review resolutions, split 80/10/10 by pair, build slices, EDA and the Module B report."""
    runner = _data_runner(run_id, config_path)
    try:
        res = runner.finalize()
    except (ModelUnavailableError, PrerequisiteError) as e:
        fail(e)
    from common.state import StageStatus
    runner.state_mgr.set_stage_status(
        "B_dataset", StageStatus.PASSED if res["passed"] else StageStatus.FAILED,
        details={"pairs_count": res["pairs"], "reviewed": True, "reviewer_agreement": res["agreement_rate"], "handcheck": res["handcheck"]},
        error=None if res["passed"] else "pass check failed")
    console.print(res)


@data_app.command("curate")
def data_curate(run_id: Optional[str] = typer.Option(None, "--run-id"),
                overrides: Optional[str] = typer.Option(None, "--overrides", help="JSON {record_id: {label, why}} curator overrides"),
                config_path: str = typer.Option("config.yaml", "--config")):
    """Vet the dataset with labelling rules v2 and write results/<run>/curated/ (originals untouched)."""
    from data.curation import curate_run, write_curation_report
    from harness.runner import load_all_scenarios
    cfg = load_config(config_path)
    run_dir = Path("results") / resolve_run_id(cfg, run_id)
    s = curate_run(cfg, run_dir, load_all_scenarios(), Path(overrides) if overrides else None)
    md, html = write_curation_report(run_dir)
    console.print(f"[bold green]Curated {s['records_out']:,} of {s['records_in']:,} records[/bold green]; reviewer agreement "
                  f"{s['reviewer_agreement']['agreement_v1'] * 100:.1f}% -> {s['reviewer_agreement']['agreement_curated'] * 100:.1f}%. Report: {html}")


@data_app.command("topup")
def data_topup(src: str = typer.Option(..., "--src"), dst: str = typer.Option(..., "--dst"),
               pairs: int = typer.Option(1000, "--pairs"), config_path: str = typer.Option("config.yaml", "--config")):
    """Reuse Module A from --src, then generate new pairs with generator v2 into --dst (resumable)."""
    from data.assemble import reuse_module_a, topup
    cfg = load_config(config_path)
    reuse_module_a(cfg, Path("results") / src, Path("results") / dst)
    s = topup(cfg, Path("results") / src, Path("results") / dst, pairs, on_progress=lambda p: console.print(
        f"[dim]{p['pairs_done']}/{p['target_pairs']} pairs, {p['failures']} skipped, ETA {p['eta_sec'] / 3600:.1f} h[/dim]"))
    console.print(f"[bold green]Top-up: {s['pairs']} pairs[/bold green] ({s['failed_pairs']} skipped), {s['sec_per_pair']} s/pair")


@data_app.command("assemble")
def data_assemble(src: str = typer.Option(..., "--src"), dst: str = typer.Option(..., "--dst"),
                  review: int = typer.Option(200, "--review"), config_path: str = typer.Option("config.yaml", "--config")):
    """Curated --src records + top-up -> blind review sample -> split -> EDA -> dataset report for --dst."""
    from data.assemble import assemble
    try:
        res = assemble(load_config(config_path), Path("results") / src, Path("results") / dst, review)
    except (PrerequisiteError, ModelUnavailableError, BudgetExceededError) as e:
        fail(e)
    console.print(res)
    if not res["passed"]:
        sys.exit(1)


@data_app.command("hardneg")
def data_hardneg(src: str = typer.Option(..., "--src"), dst: str = typer.Option(..., "--dst"),
                 config_path: str = typer.Option("config.yaml", "--config")):
    """--src dataset relabelled with rules v2.1 + self-labelled hard negatives -> --dst splits, slices and report."""
    from data.assemble import assemble_hard
    try:
        res = assemble_hard(load_config(config_path), Path("results") / src, Path("results") / dst)
    except (PrerequisiteError, ModelUnavailableError) as e:
        fail(e)
    console.print(res)
    if not res["passed"]:
        sys.exit(1)


@data_app.command("handcheck-label")
def data_handcheck_label(run_id: Optional[str] = typer.Option(None, "--run-id"),
                         config_path: str = typer.Option("config.yaml", "--config")):
    """Label the ~100 hand-check records yourself (blind); progress is saved after every answer."""
    from data import handcheck
    runner = _data_runner(run_id, config_path)
    handcheck.label_interactively(runner.run_dir)


@data_app.command("run")
def data_run(
    pairs: Optional[int] = typer.Option(None, "--target-pairs"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """generate -> review -> split in one go (stops with an error if any real component is missing)."""
    runner = _data_runner(run_id, config_path)
    try:
        res = runner.run_full_pipeline(target_pairs=pairs)
    except (PrerequisiteError, ModelUnavailableError, MockDetectedError, BudgetExceededError) as e:
        fail(e)
    console.print(res)


@app.command("train")
def train(
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID for results"),
    config_path: str = typer.Option("config.yaml", "--config", help="Config YAML file path")
):
    """Train Clef-flash safety gate model with C1 Cross-Entropy + C2 Pairwise RLCD Margin Loss."""
    cfg = load_config(config_path)
    from gate.runner import GateTrainingRunner

    runner = GateTrainingRunner(config=cfg, run_id=resolve_run_id(cfg, run_id))
    try:
        console.print("[cyan]Initiating Module C — Gate Model Training...[/cyan]")
        confirm = runner.check_confirm_path()
        console.print(f"[bold green]Path Selected:[/bold green] {confirm['path_label']}")
        console.print(f"[dim]{confirm['details']}[/dim]\n")

        def on_progress(p: dict):
            if p.get("type") == "epoch":
                ep = p.get("summary", {})
                console.print(
                    f"[bold cyan]Epoch {ep.get('epoch')}[/bold cyan] | "
                    f"Train Loss: {ep.get('train_loss'):.4f} | "
                    f"Val Loss: {ep.get('val_loss'):.4f} | "
                    f"Val Acc: [green]{ep.get('val_accuracy')*100:.1f}%[/green] | "
                    f"Harmful Pass-Through: [bold yellow]{ep.get('harmful_pass_through')*100:.2f}%[/bold yellow] | "
                    f"Benign False-Blocks: [bold magenta]{ep.get('benign_false_blocks')*100:.2f}%[/bold magenta]"
                )

        res = runner.run_training_pipeline(on_progress_callback=on_progress)
        tuning = res.get("threshold_tuning", {})
        console.print(f"\n[bold green]Training Completed Successfully![/bold green]")
        console.print(f"Best Checkpoint: [underline]{res.get('best_checkpoint')}[/underline]")
        console.print(
            f"Frozen Thresholds: [bold]tau_allow = {tuning.get('tau_allow')}[/bold], "
            f"[bold]tau_block = {tuning.get('tau_block')}[/bold]"
        )
        console.print(
            f"Validation Harmful Pass-Through: [bold green]{tuning.get('harmful_pass_through')*100:.2f}%[/bold green] (target < 1%)"
        )
        console.print(
            f"Validation Benign False-Blocks: [bold green]{tuning.get('benign_false_blocks')*100:.2f}%[/bold green] (target < 5%)"
        )
        console.print(f"Report: [underline]results/{run_id}/training_report.html[/underline]")
    except (PrerequisiteError, ModelUnavailableError) as e:
        fail(e)


@app.command("eval")
def eval_cmd(
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Module D step 1: offline metrics + baselines (trained gate, zero-shot Clef-flash, Gemma judge)."""
    cfg = load_config(config_path)
    rid = resolve_run_id(cfg, run_id)
    from eval.offline import OfflineEvaluator
    try:
        PipelineStateManager(run_id=rid).check_prerequisites_for_stage("D_evaluation")
        res = OfflineEvaluator(cfg, rid).evaluate_all(lambda p: console.print(f"[dim]{p['message']}[/dim]"))
    except (PrerequisiteError, ModelUnavailableError) as e:
        fail(e)
    table = Table(title="Module D - offline metrics (trained gate)")
    for col in ("Set", "N", "Harmful pass-through", "Benign false blocks", "Accuracy", "ECE"):
        table.add_column(col)
    for name, m in (("test", res["main_test_metrics"]), ("decomposed", res["decomposed_slice_metrics"]),
                    ("heldout_harness", res["heldout_harness_metrics"])):
        table.add_row(name, str(m["total_samples"]), f"{m['harmful_pass_through_rate'] * 100:.2f}% {m['harmful_pass_through_ci95']}",
                      f"{m['benign_false_block_rate'] * 100:.2f}% {m['benign_false_block_ci95']}", f"{m['accuracy'] * 100:.1f}%", str(m["ece"]))
    console.print(table)


@app.command("e2e")
def e2e(
    trials: Optional[int] = typer.Option(None, "--trials", "-n", help="Trials per condition"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    rerun_ungated: Optional[bool] = typer.Option(None, "--rerun-ungated/--keep-ungated",
                                                  help="What to do if the ungated replication is stale (asked interactively if omitted)"),
    config_path: str = typer.Option("config.yaml", "--config"),
):
    """Module D: offline metrics, then gated harness trials, error analysis and eval_report."""
    cfg = load_config(config_path)
    rid = resolve_run_id(cfg, run_id)
    from eval.runner import EvaluationRunner

    runner = EvaluationRunner(config=cfg, run_id=rid)
    kw = dict(trials_per_condition=trials, on_progress_callback=lambda p: console.print(f"[dim]{p['message']}[/dim]"),
              on_trial_callback=lambda u: console.print(
                  f"[dim]{u['completed']}/{u['total']} {u['current_trial']['scenario_id']} {u['current_trial']['variant']}/"
                  f"{u['current_trial']['framing']} -> {u['current_trial']['final_score']} (ETA {u['eta_sec'] / 3600:.1f} h)[/dim]"))
    try:
        try:
            res = runner.run_full_evaluation(rerun_ungated=rerun_ungated, **kw)
        except StaleUngatedRunError as e:
            console.print(f"[yellow]{e}[/yellow]")
            res = runner.run_full_evaluation(rerun_ungated=typer.confirm("Re-run the ungated replication now?"), **kw)
    except (PrerequisiteError, ModelUnavailableError, MockDetectedError) as e:
        fail(e)
    t = res["metrics"]["targets"]
    console.print(f"Harmful pass-through {t['harmful_pass_through_achieved'] * 100:.2f}% (target met: {t['pass_through_met']}); "
                  f"benign false blocks {t['benign_false_blocks_achieved'] * 100:.2f}% (target met: {t['false_blocks_met']})")
    console.print(f"Report: {res['reports']['html']}")


@app.command("compare")
def compare_cmd(base: str = typer.Option(..., "--base"), new: str = typer.Option(..., "--new"),
                config_path: str = typer.Option("config.yaml", "--config")):
    """Score --base's gate on --new's evaluation records and write comparison_report for --new."""
    from eval.compare import compare
    md, html = compare(load_config(config_path), base, new)
    console.print(f"Comparison report: {html}")


@app.command("report")
def report(
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID for results"),
    config_path: str = typer.Option("config.yaml", "--config", help="Config YAML file path")
):
    """Generate final comprehensive report linking Modules A through D (§8.1)."""
    from common.final_report import FinalReportGenerator
    run_id = resolve_run_id(load_config(config_path), run_id)
    try:
        console.print(f"[cyan]Generating final cohesive report for run_id='{run_id}'...[/cyan]")
        generator = FinalReportGenerator(run_id=run_id)
        res = generator.generate_all()
        console.print(f"[bold green]Final Cohesive Report Generated Successfully![/bold green]")
        console.print(f"HTML Report: [underline]{res['html']}[/underline]")
        console.print(f"Markdown:    [underline]{res['md']}[/underline]")
        console.print(f"Legacy MD:   [underline]{res['report_md']}[/underline]")
    except PrerequisiteError as e:
        console.print(f"[bold red]PrerequisiteError:[/bold red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Report Generation Failed:[/bold red] {e}")
        sys.exit(1)


@app.command("ui")
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind server"),
    port: int = typer.Option(8000, "--port", help="Port to bind server")
):
    """Launch the local web UI application."""
    import uvicorn
    console.print(f"[bold green]Starting Safety Gate Web UI at http://{host}:{port}[/bold green]")
    uvicorn.run("ui.app:app", host=host, port=port, reload=True, reload_dirs=["ui", "harness", "common"])


if __name__ == "__main__":
    app()
