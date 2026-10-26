import asyncio
import csv
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from common.exceptions import PrerequisiteError, BudgetExceededError, StaleUngatedRunError
from common.state import PipelineStateManager, StageStatus, STAGE_ORDER
from budget.tracker import BudgetTracker
from harness.runner import HarnessRunner, load_all_scenarios
from harness.schema import ROBOT_TOOLS_SCHEMA
from data.runner import DatasetPipelineRunner

app = FastAPI(title="Robot Safety Gate UI")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

RESULTS_DIR = Path("results")
def _config_run_id() -> str:
    cfg = Path("config.yaml")
    return yaml.safe_load(cfg.read_text()).get("project", {}).get("run_id", "run_002") if cfg.exists() else "run_002"


DEFAULT_RUN_ID = _config_run_id()

# Global in-memory broadcast queue for SSE logs
log_queues: list[asyncio.Queue] = []


async def broadcast_event(event_type: str, data: dict[str, Any]):
    payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    for q in list(log_queues):
        try:
            await q.put(payload)
        except Exception:
            pass


def get_config() -> dict[str, Any]:
    cfg_path = Path("config.yaml")
    if cfg_path.exists():
        return yaml.safe_load(cfg_path.read_text())
    return {}


@app.get("/")
def get_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        html = index_file.read_text().replace("run_001", DEFAULT_RUN_ID)
        return HTMLResponse(html.replace("</head>", f'<script>window.SG_RUN_ID = "{DEFAULT_RUN_ID}";</script></head>', 1))
    return HTMLResponse("<h1>Safety Gate UI loading...</h1>")


@app.get("/api/state")
def get_state(run_id: str = DEFAULT_RUN_ID):
    mgr = PipelineStateManager(run_id=run_id)
    return mgr.to_dict()


@app.post("/api/stage/advance")
def advance_stage(payload: dict[str, str]):
    run_id = payload.get("run_id", DEFAULT_RUN_ID)
    target_stage = payload.get("target_stage")
    mgr = PipelineStateManager(run_id=run_id)

    if not target_stage or target_stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid target stage")

    try:
        mgr.check_prerequisites_for_stage(target_stage)
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    mgr.state["active_stage"] = target_stage
    mgr._save()
    return {"active_stage": target_stage, "state": mgr.to_dict()}


@app.get("/api/budget")
def get_budget(run_id: str = DEFAULT_RUN_ID):
    tracker = BudgetTracker(run_id=run_id)
    return tracker.to_dict()


@app.get("/api/scenarios")
def get_scenarios():
    scenarios = load_all_scenarios()
    return {
        "scenarios": scenarios,
        "tools_schema": ROBOT_TOOLS_SCHEMA
    }


@app.get("/api/harness/conditions")
def get_conditions(run_id: str = DEFAULT_RUN_ID):
    cfg = get_config()
    scenarios = load_all_scenarios()
    models = cfg.get("harness", {}).get("models", [])
    trials_per_cond = cfg.get("harness", {}).get("trials_per_condition", 20)

    conditions = []
    for m in models:
        for scen_id in scenarios:
            conditions.append({"model": m["id"], "scenario": scen_id, "variant": "harmful", "framing": "real", "trials": trials_per_cond})
            conditions.append({"model": m["id"], "scenario": scen_id, "variant": "harmful", "framing": "sim", "trials": trials_per_cond})
            conditions.append({"model": m["id"], "scenario": scen_id, "variant": "harmful", "framing": "chat", "trials": trials_per_cond})
            conditions.append({"model": m["id"], "scenario": scen_id, "variant": "benign_twin", "framing": "real", "trials": trials_per_cond})
            conditions.append({"model": m["id"], "scenario": scen_id, "variant": "benign_twin", "framing": "sim", "trials": trials_per_cond})

    total_trials = len(conditions) * trials_per_cond
    # runtime estimate from measured trial durations when available, otherwise a stated prior (~45 s/trial for a 27B local model)
    durs = [json.loads(l).get("duration_sec", 0) for l in
            (RESULTS_DIR / run_id / "trials.jsonl").read_text().splitlines()[:500]] if (RESULTS_DIR / run_id / "trials.jsonl").exists() else []
    sec_per_trial = (sum(durs) / len(durs)) if durs else 45.0
    est_sec = total_trials * sec_per_trial
    return {
        "models_count": len(models),
        "scenarios_count": len(scenarios),
        "conditions_count": len(conditions),
        "trials_per_condition": trials_per_cond,
        "total_trials": total_trials,
        "estimated_runtime_seconds": round(est_sec, 1),
        "runtime_estimate_basis": "measured" if durs else "prior (45 s/trial)",
        "conditions_matrix": conditions
    }


@app.post("/api/harness/dry-run")
async def run_dry_run(payload: dict[str, Any] = None):
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    cfg = get_config()
    runner = HarnessRunner(config=cfg, run_id=run_id)

    await broadcast_event("log", {"level": "INFO", "message": "Starting Module A dry-run validation on all 10 scenes..."})
    res = runner.run_dry_run_all()
    for r in res["results"]:
        status_txt = "PASS" if r["passed"] else "FAIL"
        await broadcast_event("log", {
            "level": "INFO" if r["passed"] else "ERROR",
            "message": f"Scene {r['scenario_id']} [{r['variant']}]: {r['final_score']} -> {status_txt}"
        })

    await broadcast_event("stage_update", {"stage": "A_harness", "dry_run_passed": res["all_passed"]})
    return res


@app.post("/api/harness/replication")
async def run_replication(payload: dict[str, Any] = None):
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    trials_n = (payload or {}).get("trials_per_condition", 20)
    cfg = get_config()
    runner = HarnessRunner(config=cfg, run_id=run_id)

    try:
        runner.state_mgr.check_prerequisites_for_stage("A_harness")
        if not runner.state_mgr.is_dry_run_passed():
            raise PrerequisiteError("Dry-run pass check must succeed before ungated replication run.")
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await broadcast_event("log", {"level": "INFO", "message": "Launching ungated RoboHarm replication study..."})

    loop = asyncio.get_running_loop()

    def on_trial(update: dict[str, Any]):
        curr = update.get("current_trial", {})
        try:
            asyncio.run_coroutine_threadsafe(
                broadcast_event("trial", {
                    "completed": update["completed"],
                    "total": update["total"],
                    "trial_id": curr.get("trial_id"),
                    "scenario": curr.get("scenario_id"),
                    "variant": curr.get("variant"),
                    "framing": curr.get("framing"),
                    "score": curr.get("final_score"),
                    "steps": curr.get("steps", [])
                }),
                loop
            )
        except Exception:
            pass

    try:
        res = await loop.run_in_executor(None, lambda: runner.run_replication(trials_per_condition=trials_n, on_trial_callback=on_trial))
    except Exception as e:
        await broadcast_event("log", {"level": "ERROR", "message": f"Replication failed: {str(e)}"})
        raise HTTPException(status_code=500, detail=str(e))

    await broadcast_event("log", {"level": "SUCCESS", "message": f"Replication completed! {res['total_trials']} trials recorded."})
    await broadcast_event("stage_update", {"stage": "A_harness", "status": "passed"})
    return res


@app.post("/api/harness/gated")
def run_gated(payload: dict[str, Any] = None):
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    cfg = get_config()
    runner = HarnessRunner(config=cfg, run_id=run_id)
    try:
        runner.run_gated()
        return {"status": "ok"}
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/harness/results")
def get_harness_results(run_id: str = DEFAULT_RUN_ID):
    res_dir = RESULTS_DIR / run_id
    results_csv = res_dir / "results_table.csv"
    chat_csv = res_dir / "chat_vs_robot_comparison.csv"

    rows = []
    if results_csv.exists():
        with open(results_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    chat_rows = []
    if chat_csv.exists():
        with open(chat_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            chat_rows = list(reader)

    return {
        "results_table": rows,
        "chat_comparison": chat_rows
    }


@app.get("/api/report/module-a")
def get_module_a_report(run_id: str = DEFAULT_RUN_ID):
    report_file = RESULTS_DIR / run_id / "harness_report.html"
    if report_file.exists():
        return HTMLResponse(report_file.read_text())
    return HTMLResponse("<p>Module A report not yet generated. Run replication first.</p>")


@app.get("/api/logs/stream")
async def stream_logs():
    q: asyncio.Queue = asyncio.Queue()
    log_queues.append(q)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"
            while True:
                msg = await q.get()
                yield msg
        except asyncio.CancelledError:
            if q in log_queues:
                log_queues.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/dataset/eda")
def get_dataset_eda(run_id: str = DEFAULT_RUN_ID):
    res_dir = RESULTS_DIR / run_id
    eda_file = res_dir / "dataset_eda.json"
    worked_file = res_dir / "rlcd_worked_examples.json"
    review_file = res_dir / "review_log.json"

    eda_data = {}
    if eda_file.exists():
        eda_data = json.loads(eda_file.read_text())

    worked_examples = []
    if worked_file.exists():
        worked_examples = json.loads(worked_file.read_text())

    review_data = {}
    if review_file.exists():
        review_data = json.loads(review_file.read_text())

    return {
        "eda": eda_data,
        "worked_examples": worked_examples,
        "review_log": review_data
    }


@app.get("/api/dataset/records")
def get_dataset_records(
    run_id: str = DEFAULT_RUN_ID,
    limit: int = 50,
    offset: int = 0,
    scenario: Optional[str] = None,
    variant: Optional[str] = None,
    label: Optional[str] = None,
    framing: Optional[str] = None
):
    dataset_file = RESULTS_DIR / run_id / "dataset.jsonl"
    if not dataset_file.exists():
        return {"total": 0, "records": []}

    matching = []
    with open(dataset_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if scenario and rec.get("scenario") != scenario:
                continue
            if variant and rec.get("variant") != variant:
                continue
            if label and rec.get("label") != label:
                continue
            if framing and rec.get("operator_framing") != framing:
                continue
            matching.append(rec)

    total_matched = len(matching)
    sliced = matching[offset:offset + limit]

    return {
        "total": total_matched,
        "offset": offset,
        "limit": limit,
        "records": sliced
    }


@app.post("/api/dataset/generate")
async def run_dataset_generation(payload: dict[str, Any] = None):
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    pairs_n = (payload or {}).get("pairs")
    cfg = get_config()
    runner = DatasetPipelineRunner(config=cfg, run_id=run_id)

    try:
        runner.state_mgr.check_prerequisites_for_stage("B_dataset")
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await broadcast_event("log", {"level": "INFO", "message": f"Starting Module B RLCD Dataset generation (target {pairs_n:,} pairs)..."})

    loop = asyncio.get_running_loop()

    def on_progress(update: dict[str, Any]):
        try:
            asyncio.run_coroutine_threadsafe(
                broadcast_event("dataset_progress", update),
                loop
            )
        except Exception:
            pass

    try:
        res = await loop.run_in_executor(
            None,
            lambda: runner.run_full_pipeline(target_pairs=pairs_n, on_progress=on_progress)
        )
    except Exception as e:
        await broadcast_event("log", {"level": "ERROR", "message": f"Dataset generation failed: {str(e)}"})
        raise HTTPException(status_code=500, detail=str(e))

    await broadcast_event("log", {
        "level": "SUCCESS",
        "message": f"Module B complete: {res['pairs']:,} pairs. Reviewer agreement {res['agreement_rate']*100:.1f}%. Hand-check: {res['handcheck']}."
    })
    await broadcast_event("stage_update", {"stage": "B_dataset", "status": "passed"})
    return res


@app.get("/api/report/module-b")
def get_module_b_report(run_id: str = DEFAULT_RUN_ID):
    report_file = RESULTS_DIR / run_id / "dataset_report.html"
    if report_file.exists():
        return HTMLResponse(report_file.read_text())
    return HTMLResponse("<p>Module B dataset report not yet generated.</p>")


# ----------------------------------------------------
# MODULE C: GATE MODEL TRAINING ENDPOINTS
# ----------------------------------------------------

@app.get("/api/training/confirm")
def get_training_confirm(run_id: str = DEFAULT_RUN_ID):
    from gate.runner import GateTrainingRunner
    cfg = get_config()
    runner = GateTrainingRunner(config=cfg, run_id=run_id)
    return runner.check_confirm_path()


@app.get("/api/training/metrics")
def get_training_metrics(run_id: str = DEFAULT_RUN_ID):
    res_dir = RESULTS_DIR / run_id
    metrics_file = res_dir / "training_metrics.json"
    curves_file = res_dir / "threshold_curves.json"
    thresh_file = res_dir / "thresholds.json"

    metrics_data = {}
    if metrics_file.exists():
        metrics_data = json.loads(metrics_file.read_text())

    curves_data = {}
    if curves_file.exists():
        curves_data = json.loads(curves_file.read_text())

    thresh_data = {}
    if thresh_file.exists():
        thresh_data = json.loads(thresh_file.read_text())

    return {
        "metrics": metrics_data,
        "curves": curves_data,
        "thresholds": thresh_data
    }


@app.post("/api/training/run")
async def run_gate_training(payload: dict[str, Any] = None):
    from gate.runner import GateTrainingRunner
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    cfg = get_config()
    runner = GateTrainingRunner(config=cfg, run_id=run_id)

    try:
        runner.state_mgr.check_prerequisites_for_stage("C_training")
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    confirm_info = runner.check_confirm_path()
    await broadcast_event("log", {
        "level": "INFO",
        "message": f"Starting Module C Gate Model Training on {confirm_info['device']} ({confirm_info['path_label']})..."
    })

    loop = asyncio.get_running_loop()

    def on_progress(update: dict[str, Any]):
        try:
            asyncio.run_coroutine_threadsafe(
                broadcast_event("training_progress", update),
                loop
            )
        except Exception:
            pass

    try:
        res = await loop.run_in_executor(
            None,
            lambda: runner.run_training_pipeline(on_progress_callback=on_progress)
        )
    except Exception as e:
        await broadcast_event("log", {"level": "ERROR", "message": f"Gate training failed: {str(e)}"})
        raise HTTPException(status_code=500, detail=str(e))

    tuning = res.get("threshold_tuning", {})
    await broadcast_event("log", {
        "level": "SUCCESS",
        "message": f"Module C Training Complete! Best checkpoint saved. Optimal thresholds frozen: tau_allow={tuning.get('tau_allow')}, tau_block={tuning.get('tau_block')}. Val pass-through: {tuning.get('harmful_pass_through')*100:.2f}%."
    })
    await broadcast_event("stage_update", {"stage": "C_training", "status": "passed"})
    return res


@app.get("/api/report/module-c")
def get_module_c_report(run_id: str = DEFAULT_RUN_ID):
    report_file = RESULTS_DIR / run_id / "training_report.html"
    if report_file.exists():
        return HTMLResponse(report_file.read_text())
    return HTMLResponse("<p>Module C training report not yet generated. Run training first.</p>")


# ----------------------------------------------------
# MODULE D: EVALUATION & END-TO-END ENDPOINTS
# ----------------------------------------------------

@app.get("/api/eval/metrics")
def get_eval_metrics(run_id: str = DEFAULT_RUN_ID):
    res_dir = RESULTS_DIR / run_id
    metrics_file = res_dir / "metrics.json"

    metrics_data = {}
    if metrics_file.exists():
        metrics_data = json.loads(metrics_file.read_text())

    return metrics_data


@app.get("/api/eval/comparison")
def get_eval_comparison(run_id: str = DEFAULT_RUN_ID):
    res_dir = RESULTS_DIR / run_id
    comp_csv = res_dir / "with_vs_without_gate_comparison.csv"
    gated_csv = res_dir / "gated_results_table.csv"

    comp_rows = []
    if comp_csv.exists():
        with open(comp_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            comp_rows = list(reader)

    gated_rows = []
    if gated_csv.exists():
        with open(gated_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            gated_rows = list(reader)

    return {
        "comparison_table": comp_rows,
        "gated_table": gated_rows
    }


@app.post("/api/eval/run")
async def run_evaluation(payload: dict[str, Any] = None):
    from eval.runner import EvaluationRunner
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    trials_n = (payload or {}).get("trials_per_condition")
    cfg = get_config()
    runner = EvaluationRunner(config=cfg, run_id=run_id)

    try:
        runner.state_mgr.check_prerequisites_for_stage("D_evaluation")
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await broadcast_event("log", {
        "level": "INFO",
        "message": "Starting Module D: Offline test evaluation & 1,000 gated robot harness trials..."
    })

    loop = asyncio.get_running_loop()

    def on_trial(update: dict[str, Any]):
        curr = update.get("current_trial", {})
        try:
            asyncio.run_coroutine_threadsafe(
                broadcast_event("gated_trial", {
                    "completed": update["completed"],
                    "total": update["total"],
                    "trial_id": curr.get("trial_id"),
                    "scenario": curr.get("scenario_id"),
                    "variant": curr.get("variant"),
                    "framing": curr.get("framing"),
                    "score": curr.get("final_score"),
                    "steps": curr.get("steps", [])
                }),
                loop
            )
        except Exception:
            pass

    def on_progress(p: dict[str, Any]):
        try:
            asyncio.run_coroutine_threadsafe(
                broadcast_event("eval_progress", p),
                loop
            )
        except Exception:
            pass

    try:
        res = await loop.run_in_executor(
            None,
            lambda: runner.run_full_evaluation(
                trials_per_condition=trials_n,
                on_trial_callback=on_trial,
                on_progress_callback=on_progress,
                rerun_ungated=(payload or {}).get("rerun_ungated")
            )
        )
    except StaleUngatedRunError as e:
        await broadcast_event("log", {"level": "WARN", "message": str(e)})
        raise HTTPException(status_code=409, detail=f"{e} POST again with rerun_ungated true/false to choose.")
    except Exception as e:
        await broadcast_event("log", {"level": "ERROR", "message": f"Evaluation failed: {str(e)}"})
        raise HTTPException(status_code=500, detail=str(e))

    m = res["metrics"]["main_test_metrics"]
    h = res["metrics"]["harness_comparison"]
    await broadcast_event("log", {
        "level": "SUCCESS",
        "message": f"Module D Complete! Test harmful pass-through: {m['harmful_pass_through_rate']*100:.2f}%, false blocks: {m['benign_false_block_rate']*100:.2f}%. Harmful completed {h['harmful_completed_ungated']['rate']} -> {h['harmful_completed_gated']['rate']} with gate; benign twin completed {h['benign_completed_ungated']['rate']} -> {h['benign_completed_gated']['rate']}."
    })
    await broadcast_event("stage_update", {"stage": "D_evaluation", "status": "passed"})
    return res


@app.get("/api/report/module-d")
def get_module_d_report(run_id: str = DEFAULT_RUN_ID):
    report_file = RESULTS_DIR / run_id / "eval_report.html"
    if report_file.exists():
        return HTMLResponse(report_file.read_text())
    return HTMLResponse("<p>Module D evaluation report not yet generated. Run evaluation first.</p>")


@app.get("/api/report/final")
def get_final_report(run_id: str = DEFAULT_RUN_ID):
    report_file = RESULTS_DIR / run_id / "final_report.html"
    if report_file.exists():
        return HTMLResponse(report_file.read_text(encoding="utf-8"))
    return HTMLResponse("<p>Final cohesive report not yet generated. Click Generate Final Report.</p>")


@app.post("/api/report/generate")
async def generate_final_report_endpoint(payload: Optional[dict[str, Any]] = None):
    from common.final_report import FinalReportGenerator
    run_id = (payload or {}).get("run_id", DEFAULT_RUN_ID)
    try:
        await broadcast_event("log", {"level": "INFO", "message": f"Compiling final publication report for {run_id}..."})
        gen = FinalReportGenerator(run_id=run_id)
        res = gen.generate_all()
        await broadcast_event("log", {"level": "SUCCESS", "message": f"Final Report generated: {res['html'].name} and {res['md'].name}"})
        await broadcast_event("stage_update", {"stage": "final_report", "status": "passed"})
        return {
            "status": "success",
            "html_path": str(res["html"]),
            "md_path": str(res["md"]),
            "report_md_path": str(res["report_md"])
        }
    except PrerequisiteError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        await broadcast_event("log", {"level": "ERROR", "message": str(e)})
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        await broadcast_event("log", {"level": "ERROR", "message": f"Final report generation failed: {str(e)}"})
        raise HTTPException(status_code=500, detail=str(e))


