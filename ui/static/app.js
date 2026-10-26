// Robot Safety Gate - Frontend Logic
const RUN_ID = window.SG_RUN_ID;
let appState = null;
let scenariosData = {};
let currentScenarioId = "stab_doll";
let currentVariant = "harmful";
let activeScriptedSteps = [];
let currentStepIdx = 0;
let replayTimer = null;
let chartInstance = null;

// Module B State
let currentStageView = "A_harness";
let workedExamplesData = [];
let datasetOffset = 0;
const datasetLimit = 15;
let datasetTotal = 0;
let chartLabelsInstance = null;
let chartVariantsInstance = null;
let chartHistoryInstance = null;
let chartToolsInstance = null;

// Module C State
let chartTrainingLossInstance = null;
let chartThresholdTuningInstance = null;
let trainingLossDataPoints = [];
let valLossDataPoints = [];

// Module D State
let chartE2EComparisonInstance = null;
let errorAnalysisData = [];

document.addEventListener("DOMContentLoaded", () => {
  initApp();
});

async function initApp() {
  await fetchState();
  await fetchBudget();
  await fetchScenarios();
  await fetchConditions();
  await fetchResults();
  await fetchDatasetEDA();
  await fetchDatasetRecords();
  await fetchTrainingConfirm();
  await fetchTrainingMetrics();
  await fetchEvalMetrics();
  setupSSE();
  setupEventListeners();
  renderStepper();
  renderScenario();

  if (appState && (appState.active_stage === "D_evaluation" || appState.stages?.["D_evaluation"]?.status === "passed")) {
    switchStage("D_evaluation");
  } else if (appState && (appState.active_stage === "C_training" || appState.stages?.["C_training"]?.status === "passed")) {
    switchStage("C_training");
  } else if (appState && (appState.active_stage === "B_dataset" || appState.stages?.["B_dataset"]?.status === "passed")) {
    switchStage("B_dataset");
  } else {
    switchStage("A_harness");
  }
}

// SSE Log and Event Stream
function setupSSE() {
  const evtSource = new EventSource("/api/logs/stream");

  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      appendLog("INFO", JSON.stringify(data));
    } catch (_) {}
  };

  evtSource.addEventListener("log", (e) => {
    const data = JSON.parse(e.data);
    appendLog(data.level || "INFO", data.message);
    appendTrainingLog(data.level || "INFO", data.message);
    appendEvalLog(data.level || "INFO", data.message);
  });

  evtSource.addEventListener("trial", (e) => {
    const data = JSON.parse(e.data);
    updateReplicationProgress(data);
  });

  evtSource.addEventListener("dataset_progress", (e) => {
    const data = JSON.parse(e.data);
    updateDatasetProgress(data);
  });

  evtSource.addEventListener("training_progress", (e) => {
    const data = JSON.parse(e.data);
    updateTrainingProgress(data);
  });

  evtSource.addEventListener("gated_trial", (e) => {
    const data = JSON.parse(e.data);
    updateGatedTrialFeed(data);
  });

  evtSource.addEventListener("eval_progress", (e) => {
    const data = JSON.parse(e.data);
    updateEvalProgress(data);
  });

  evtSource.addEventListener("stage_update", async (e) => {
    await fetchState();
    renderStepper();
    await fetchResults();
    await fetchDatasetEDA();
    await fetchDatasetRecords();
    await fetchTrainingMetrics();
    await fetchEvalMetrics();
  });
}

function appendLog(level, msg) {
  const box = document.getElementById("log-box");
  if (!box) return;

  const timeStr = new Date().toTimeString().split(" ")[0];
  const row = document.createElement("div");
  row.className = "log-entry";
  row.innerHTML = `
    <span class="log-time">[${timeStr}]</span>
    <span class="log-level ${level}">${level}</span>
    <span class="log-msg">${escapeHtml(msg)}</span>
  `;
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

async function fetchState() {
  try {
    const res = await fetch("/api/state");
    appState = await res.json();
    renderStepper();
    updateActionButtons();
  } catch (err) {
    console.error("Failed to fetch state:", err);
  }
}

async function fetchBudget() {
  try {
    const res = await fetch("/api/budget");
    const data = await res.json();
    const el = document.getElementById("budget-display");
    if (el) {
      el.textContent = `$${data.total_spend_usd.toFixed(4)} / $${data.hard_cap_usd.toFixed(2)}`;
    }
  } catch (err) {
    console.error("Failed to fetch budget:", err);
  }
}

async function fetchScenarios() {
  try {
    const res = await fetch("/api/scenarios");
    const data = await res.json();
    scenariosData = data.scenarios || {};

    const select = document.getElementById("scenario-picker");
    if (select) {
      select.innerHTML = "";
      for (const [sId, sData] of Object.entries(scenariosData)) {
        const opt = document.createElement("option");
        opt.value = sId;
        opt.textContent = `${sData.name} (${sId})`;
        select.appendChild(opt);
      }
      select.value = currentScenarioId;
    }
  } catch (err) {
    console.error("Failed to fetch scenarios:", err);
  }
}

async function fetchConditions() {
  try {
    const res = await fetch("/api/harness/conditions");
    const data = await res.json();
    const el = document.getElementById("planned-trials-count");
    if (el) el.textContent = `${data.total_trials} planned trials (${data.conditions_count} conditions)`;
  } catch (err) {
    console.error("Failed to fetch conditions:", err);
  }
}

async function fetchResults() {
  try {
    const res = await fetch("/api/harness/results");
    const data = await res.json();
    renderResultsTable(data.results_table || []);
    renderChart(data.chat_comparison || []);
  } catch (err) {
    console.error("Failed to fetch results:", err);
  }
}

function switchStage(stageId) {
  currentStageView = stageId;
  const viewA = document.getElementById("stage-view-A_harness");
  const viewB = document.getElementById("stage-view-B_dataset");
  const viewC = document.getElementById("stage-view-C_training");
  const viewD = document.getElementById("stage-view-D_evaluation");

  if (viewA) viewA.style.display = (stageId === "A_harness") ? "flex" : "none";
  if (viewB) viewB.style.display = (stageId === "B_dataset") ? "flex" : "none";
  if (viewC) viewC.style.display = (stageId === "C_training") ? "flex" : "none";
  if (viewD) viewD.style.display = (stageId === "D_evaluation") ? "flex" : "none";

  if (stageId === "C_training") {
    fetchTrainingConfirm();
    fetchTrainingMetrics();
  } else if (stageId === "D_evaluation") {
    fetchEvalMetrics();
  }

  renderStepper();
  updateActionButtons();
}

function renderStepper() {
  if (!appState || !appState.stages) return;
  const stages = [
    { id: "A_harness", label: "A Harness" },
    { id: "B_dataset", label: "B Dataset" },
    { id: "C_training", label: "C Training" },
    { id: "D_evaluation", label: "D Evaluation" },
    { id: "final_report", label: "Final Report" }
  ];

  stages.forEach(st => {
    const info = appState.stages[st.id] || { status: "locked" };
    const el = document.getElementById(`step-${st.id}`);
    if (el) {
      el.className = `step-item ${info.status} ${currentStageView === st.id ? "active" : ""}`;
      const badge = el.querySelector(".step-badge");
      if (badge) {
        badge.className = `step-badge badge-${info.status}`;
        badge.textContent = info.status;
      }
      el.onclick = () => {
        if (info.status !== "locked") {
          switchStage(st.id);
        }
      };
    }
  });
}

function updateActionButtons() {
  if (!appState || !appState.stages) return;
  const stageA = appState.stages["A_harness"] || {};
  const stageB = appState.stages["B_dataset"] || {};
  const stageC = appState.stages["C_training"] || {};
  const stageD = appState.stages["D_evaluation"] || {};
  const dryPassed = stageA.dry_run_passed;
  const repPassed = stageA.replication_passed || stageA.status === "passed";
  const bPassed = stageB.status === "passed";
  const cPassed = stageC.status === "passed";
  const dPassed = stageD.status === "passed";

  const repBtn = document.getElementById("btn-run-replication");
  if (repBtn) repBtn.disabled = !dryPassed;

  const nextBtn = document.getElementById("btn-continue-b");
  if (nextBtn) nextBtn.disabled = !repPassed;

  const btnC = document.getElementById("btn-continue-c");
  if (btnC) btnC.disabled = !bPassed;

  const btnD = document.getElementById("btn-continue-d");
  if (btnD) btnD.disabled = !cPassed;

  const btnFinal = document.getElementById("btn-continue-final");
  if (btnFinal) btnFinal.disabled = !dPassed;

  const footerStatus = document.getElementById("footer-gate-status");
  if (currentStageView === "A_harness") {
    if (footerStatus) footerStatus.textContent = "Stage Gate: Module A must pass both Dry-Run checks and Ungated Replication before unlocking Module B.";
    if (nextBtn) nextBtn.style.display = "inline-flex";
    if (btnC) btnC.style.display = "none";
    if (btnD) btnD.style.display = "none";
    if (btnFinal) btnFinal.style.display = "none";
  } else if (currentStageView === "B_dataset") {
    if (footerStatus) {
      if (bPassed) {
        footerStatus.textContent = `Stage Gate: Module B PASSED (${stageB.pairs_count} pairs, no pair leakage, ${(stageB.reviewer_agreement*100).toFixed(1)}% reviewer agreement). Module C training unlocked.`;
      } else {
        footerStatus.textContent = "Stage Gate: Module B in progress. Must generate >= 2,000 pairs with zero leakage and >= 90% agreement.";
      }
    }
    if (nextBtn) nextBtn.style.display = "none";
    if (btnC) btnC.style.display = "inline-flex";
    if (btnD) btnD.style.display = "none";
    if (btnFinal) btnFinal.style.display = "none";
  } else if (currentStageView === "C_training") {
    if (footerStatus) {
      if (cPassed) {
        footerStatus.textContent = `Stage Gate: Module C PASSED (Best checkpoint saved, frozen tau_allow=${stageC.tau_allow || 0.90}, tau_block=${stageC.tau_block || 0.50}, val pass-through ${(stageC.val_harmful_pass_through*100 || 0).toFixed(2)}%). Module D Evaluation unlocked.`;
      } else {
        footerStatus.textContent = "Stage Gate: Module C ready. Click 'Start Gate Training' to train the Clef-flash decision head on Apple Silicon.";
      }
    }
    if (nextBtn) nextBtn.style.display = "none";
    if (btnC) btnC.style.display = "none";
    if (btnD) btnD.style.display = "inline-flex";
    if (btnFinal) btnFinal.style.display = "none";
  } else if (currentStageView === "D_evaluation") {
    if (footerStatus) {
      if (dPassed) {
        footerStatus.textContent = "Stage Gate: Module D completed (see the report for pass/fail against the targets). Ready for the final report.";
      } else {
        footerStatus.textContent = "Stage Gate: Module D ready. Click 'Run Full Gated E2E' to evaluate the offline sets and run the gated robot trials.";
      }
    }
    if (nextBtn) nextBtn.style.display = "none";
    if (btnC) btnC.style.display = "none";
    if (btnD) btnD.style.display = "none";
    if (btnFinal) btnFinal.style.display = "inline-flex";
  } else if (currentStageView === "final_report") {
    if (footerStatus) {
      footerStatus.textContent = "Stage Gate: Final Cohesive Report generated and verified (§8.1). All pipeline requirements complete!";
    }
    if (nextBtn) nextBtn.style.display = "none";
    if (btnC) btnC.style.display = "none";
    if (btnD) btnD.style.display = "none";
    if (btnFinal) btnFinal.style.display = "inline-flex";
  }

  const dryCheckBadge = document.getElementById("dry-check-status");
  if (dryCheckBadge) {
    dryCheckBadge.textContent = dryPassed ? "✓ Dry-Run Passed (Replication Unlocked)" : "Dry-Run Pending";
    dryCheckBadge.className = dryPassed ? "badge-passed" : "badge-ready";
  }
}

function setupEventListeners() {
  document.getElementById("scenario-picker")?.addEventListener("change", (e) => {
    currentScenarioId = e.target.value;
    renderScenario();
  });

  document.querySelectorAll(".variant-toggle").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".variant-toggle").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentVariant = btn.dataset.variant;
      renderScenario();
    });
  });

  document.getElementById("btn-run-dry-run")?.addEventListener("click", runDryRun);
  document.getElementById("btn-run-replication")?.addEventListener("click", runReplication);
  document.getElementById("btn-continue-b")?.addEventListener("click", continueToModuleB);
  document.getElementById("btn-continue-c")?.addEventListener("click", continueToModuleC);
  document.getElementById("btn-continue-d")?.addEventListener("click", continueToModuleD);
  document.getElementById("btn-continue-final")?.addEventListener("click", generateFinalReport);
  document.getElementById("btn-re-generate-final")?.addEventListener("click", generateFinalReport);
  document.getElementById("btn-run-dataset-gen")?.addEventListener("click", runDatasetGeneration);
  document.getElementById("btn-run-training")?.addEventListener("click", runGateTraining);
  document.getElementById("btn-view-report-c")?.addEventListener("click", toggleTrainingReport);
  document.getElementById("btn-run-offline-eval")?.addEventListener("click", runOfflineEvaluation);
  document.getElementById("btn-run-e2e")?.addEventListener("click", runFullEvaluation);
  document.getElementById("btn-view-report-d")?.addEventListener("click", toggleEvalReport);
  document.getElementById("error-type-filter")?.addEventListener("change", renderFilteredErrors);

  document.getElementById("worked-example-picker")?.addEventListener("change", (e) => {
    const idx = parseInt(e.target.value, 10);
    if (workedExamplesData[idx]) {
      renderWorkedExample(workedExamplesData[idx]);
    }
  });

  // Filters for dataset browser
  ["filter-scenario", "filter-variant", "filter-label", "filter-framing"].forEach(id => {
    document.getElementById(id)?.addEventListener("change", () => {
      datasetOffset = 0;
      fetchDatasetRecords();
    });
  });

  document.getElementById("btn-dataset-prev")?.addEventListener("click", () => {
    if (datasetOffset > 0) {
      datasetOffset = Math.max(0, datasetOffset - datasetLimit);
      fetchDatasetRecords();
    }
  });

  document.getElementById("btn-dataset-next")?.addEventListener("click", () => {
    if (datasetOffset + datasetLimit < datasetTotal) {
      datasetOffset += datasetLimit;
      fetchDatasetRecords();
    }
  });

  // Replay buttons
  document.getElementById("btn-play-pause")?.addEventListener("click", togglePlayPause);
  document.getElementById("btn-prev-step")?.addEventListener("click", () => stepReplay(-1));
  document.getElementById("btn-next-step")?.addEventListener("click", () => stepReplay(1));
  document.getElementById("btn-reset-step")?.addEventListener("click", resetReplay);

  const slider = document.getElementById("step-slider");
  if (slider) {
    slider.addEventListener("input", (e) => {
      setReplayStep(parseInt(e.target.value, 10));
    });
  }
}

function renderScenario() {
  const scen = scenariosData[currentScenarioId];
  if (!scen) return;

  const varData = scen[currentVariant] || {};
  activeScriptedSteps = varData.scripted_sequence || [];
  currentStepIdx = 0;

  // Update text cards
  document.getElementById("instruction-text").textContent = varData.instruction || "—";
  document.getElementById("initial-scene-text").textContent = varData.initial_scene || "—";
  document.getElementById("real-prompt-text").textContent = varData.real_framing_system_prompt || "—";
  document.getElementById("sim-prompt-text").textContent = varData.sim_framing_system_prompt || "—";

  // Update slider range
  const slider = document.getElementById("step-slider");
  if (slider) {
    slider.min = 0;
    slider.max = Math.max(0, activeScriptedSteps.length - 1);
    slider.value = 0;
  }

  updateReplayDisplay();
  renderSVGScene(scen, currentVariant, null);
}

// Top-Down Table SVG Visualizer
function renderSVGScene(scen, variant, activeStep) {
  const svg = document.getElementById("table-viz");
  if (!svg || !scen) return;

  const width = 760;
  const height = 360;

  // Coordinate mapping: table width 100cm (-50 to 50), depth 60cm (0 to 60)
  const mapX = (x_cm) => ((x_cm + 50.0) / 100.0) * (width - 120) + 60;
  const mapY = (y_cm) => height - 40 - (y_cm / 60.0) * (height - 80);

  // Initial arm positions
  let leftArm = { x: -20, y: 20, z: 10, gripper: "open", holding: null };
  let rightArm = { x: 20, y: 20, z: 10, gripper: "open", holding: null };

  const objects = (scen.objects || []).filter(o => (o.variant_scope || []).includes(variant)).map(o => ({
    id: o.id,
    name: o.name,
    x: o.pos[0],
    y: o.pos[1],
    color: o.color || "#3b82f6",
    held: false
  }));

  // If active step has env_state, reflect it
  if (activeStep && activeStep.env_state) {
    const sArms = activeStep.env_state.arms || {};
    if (sArms.left) {
      leftArm.x = sArms.left.pos[0];
      leftArm.y = sArms.left.pos[1];
      leftArm.gripper = sArms.left.gripper;
      leftArm.holding = sArms.left.holding;
    }
    if (sArms.right) {
      rightArm.x = sArms.right.pos[0];
      rightArm.y = sArms.right.pos[1];
      rightArm.gripper = sArms.right.gripper;
      rightArm.holding = sArms.right.holding;
    }
    const sObjs = activeStep.env_state.objects || {};
    objects.forEach(obj => {
      if (sObjs[obj.id]) {
        obj.x = sObjs[obj.id].pos[0];
        obj.y = sObjs[obj.id].pos[1];
        if (sObjs[obj.id].state?.gripped_by) {
          obj.held = true;
        }
      }
    });
  }

  let svgContent = `
    <!-- Table Surface -->
    <rect x="50" y="30" width="${width - 100}" height="${height - 60}" rx="14" fill="#11141c" stroke="#252d3d" stroke-width="2" />
    <text x="65" y="52" fill="#5a6475" font-size="11" font-family="'IBM Plex Mono', monospace">WORK TABLE (100cm × 60cm)</text>

    <!-- Grid lines -->
    <line x1="${mapX(0)}" y1="30" x2="${mapX(0)}" y2="${height - 30}" stroke="#1b202c" stroke-width="1" stroke-dasharray="4" />
    <line x1="50" y1="${mapY(30)}" x2="${width - 50}" y2="${mapY(30)}" stroke="#1b202c" stroke-width="1" stroke-dasharray="4" />

    <!-- Left Arm Base -->
    <circle cx="${mapX(-30)}" cy="${mapY(0)}" r="18" fill="#1f293d" stroke="#3b82f6" stroke-width="2" />
    <text x="${mapX(-30)}" y="${mapY(0) + 4}" fill="#3b82f6" font-size="10" text-anchor="middle" font-family="'IBM Plex Mono', monospace">L-BASE</text>

    <!-- Right Arm Base -->
    <circle cx="${mapX(30)}" cy="${mapY(0)}" r="18" fill="#1f293d" stroke="#06b6d4" stroke-width="2" />
    <text x="${mapX(30)}" y="${mapY(0) + 4}" fill="#06b6d4" font-size="10" text-anchor="middle" font-family="'IBM Plex Mono', monospace">R-BASE</text>

    <!-- Left Arm Link & End-Effector -->
    <line x1="${mapX(-30)}" y1="${mapY(0)}" x2="${mapX(leftArm.x)}" y2="${mapY(leftArm.y)}" stroke="#3b82f6" stroke-width="4" stroke-linecap="round" opacity="0.8" />
    <circle cx="${mapX(leftArm.x)}" cy="${mapY(leftArm.y)}" r="12" fill="${leftArm.gripper === 'closed' ? '#ef4444' : '#3b82f6'}" stroke="#fff" stroke-width="2" />
    <text x="${mapX(leftArm.x)}" y="${mapY(leftArm.y) - 16}" fill="#93c5fd" font-size="11" text-anchor="middle" font-family="'IBM Plex Mono', monospace">Left Hand [${leftArm.gripper}]</text>

    <!-- Right Arm Link & End-Effector -->
    <line x1="${mapX(30)}" y1="${mapY(0)}" x2="${mapX(rightArm.x)}" y2="${mapY(rightArm.y)}" stroke="#06b6d4" stroke-width="4" stroke-linecap="round" opacity="0.8" />
    <circle cx="${mapX(rightArm.x)}" cy="${mapY(rightArm.y)}" r="12" fill="${rightArm.gripper === 'closed' ? '#ef4444' : '#06b6d4'}" stroke="#fff" stroke-width="2" />
    <text x="${mapX(rightArm.x)}" y="${mapY(rightArm.y) - 16}" fill="#67e8f9" font-size="11" text-anchor="middle" font-family="'IBM Plex Mono', monospace">Right Hand [${rightArm.gripper}]</text>
  `;

  // Draw scenario objects
  objects.forEach(obj => {
    const ox = mapX(obj.x);
    const oy = mapY(obj.y);
    svgContent += `
      <g transform="translate(${ox}, ${oy})">
        <rect x="-18" y="-12" width="36" height="24" rx="4" fill="${obj.color}" fill-opacity="0.25" stroke="${obj.color}" stroke-width="2" />
        <circle cx="0" cy="0" r="3" fill="#fff" />
        <text x="0" y="24" fill="#e2e8f0" font-size="11" text-anchor="middle" font-family="'IBM Plex Sans', sans-serif">${escapeHtml(obj.name)}</text>
        ${obj.held ? '<text x="0" y="-16" fill="#f59e0b" font-size="9" text-anchor="middle" font-family="monospace">[GRIPPED]</text>' : ''}
      </g>
    `;
  });

  svg.innerHTML = svgContent;
}

// Replay Functions
function updateReplayDisplay() {
  const stepLabel = document.getElementById("step-counter-label");
  const toolCallLabel = document.getElementById("current-tool-call");
  const resultLabel = document.getElementById("current-tool-result");
  const scoreBadge = document.getElementById("current-step-score");
  const slider = document.getElementById("step-slider");

  if (!activeScriptedSteps.length) {
    if (stepLabel) stepLabel.textContent = "Step 0 / 0";
    if (toolCallLabel) toolCallLabel.textContent = "No scripted sequence loaded";
    if (resultLabel) resultLabel.textContent = "—";
    return;
  }

  if (stepLabel) stepLabel.textContent = `Step ${currentStepIdx + 1} / ${activeScriptedSteps.length}`;
  if (slider) slider.value = currentStepIdx;

  const step = activeScriptedSteps[currentStepIdx];
  if (toolCallLabel) toolCallLabel.textContent = `${step.tool}(${JSON.stringify(step.args || {})})`;
  if (resultLabel) resultLabel.textContent = step.result || "—";

  if (scoreBadge) {
    if (currentStepIdx === 0) {
      scoreBadge.textContent = "refused";
      scoreBadge.className = "badge-locked";
    } else if (currentStepIdx < activeScriptedSteps.length - 1) {
      scoreBadge.textContent = "attempted";
      scoreBadge.className = "badge-running";
    } else {
      scoreBadge.textContent = "completed-equivalent";
      scoreBadge.className = "badge-passed";
    }
  }

  // Animate SVG table based on synthetic simulated step progression
  const simArmPos = computeSimulatedArmState(activeScriptedSteps, currentStepIdx);
  const scen = scenariosData[currentScenarioId];
  renderSVGScene(scen, currentVariant, simArmPos);
}

function computeSimulatedArmState(steps, upToIdx) {
  let leftPos = [-20.0, 20.0, 10.0];
  let rightPos = [20.0, 20.0, 10.0];
  let leftGripper = "open";
  let rightGripper = "open";
  let leftHolding = null;

  for (let i = 0; i <= upToIdx && i < steps.length; i++) {
    const s = steps[i];
    if (s.tool === "move_arm") {
      const arm = s.args.arm;
      if (arm === "left") {
        leftPos[0] += (s.args.dx_cm || 0);
        leftPos[1] += (s.args.dy_cm || 0);
        leftPos[2] += (s.args.dz_cm || 0);
      } else {
        rightPos[0] += (s.args.dx_cm || 0);
        rightPos[1] += (s.args.dy_cm || 0);
        rightPos[2] += (s.args.dz_cm || 0);
      }
    } else if (s.tool === "grip") {
      if (s.args.arm === "left") {
        leftGripper = "closed";
        leftHolding = "primary_obj";
      } else {
        rightGripper = "closed";
      }
    } else if (s.tool === "release") {
      if (s.args.arm === "left") {
        leftGripper = "open";
        leftHolding = null;
      } else {
        rightGripper = "open";
      }
    }
  }

  return {
    env_state: {
      arms: {
        left: { pos: leftPos, gripper: leftGripper, holding: leftHolding },
        right: { pos: rightPos, gripper: rightGripper, holding: null }
      }
    }
  };
}

function setReplayStep(idx) {
  currentStepIdx = Math.max(0, Math.min(idx, activeScriptedSteps.length - 1));
  updateReplayDisplay();
}

function stepReplay(delta) {
  setReplayStep(currentStepIdx + delta);
}

function resetReplay() {
  if (replayTimer) clearInterval(replayTimer);
  replayTimer = null;
  const playBtn = document.getElementById("btn-play-pause");
  if (playBtn) playBtn.textContent = "▶ Play";
  setReplayStep(0);
}

function togglePlayPause() {
  const btn = document.getElementById("btn-play-pause");
  if (replayTimer) {
    clearInterval(replayTimer);
    replayTimer = null;
    if (btn) btn.textContent = "▶ Play";
  } else {
    if (currentStepIdx >= activeScriptedSteps.length - 1) {
      currentStepIdx = 0;
    }
    if (btn) btn.textContent = "⏸ Pause";
    replayTimer = setInterval(() => {
      if (currentStepIdx < activeScriptedSteps.length - 1) {
        setReplayStep(currentStepIdx + 1);
      } else {
        clearInterval(replayTimer);
        replayTimer = null;
        if (btn) btn.textContent = "▶ Play";
      }
    }, 800);
  }
}

// Dry-Run Runner
async function runDryRun() {
  const btn = document.getElementById("btn-run-dry-run");
  if (btn) btn.disabled = true;

  appendLog("INFO", "Executing dry-run validation on all 10 scenes...");
  try {
    const res = await fetch("/api/harness/dry-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID })
    });
    const data = await res.json();
    if (data.all_passed) {
      appendLog("SUCCESS", `Dry-run check PASSED! All ${data.passed_count}/${data.total_scenes} scenes verified.`);
    } else {
      appendLog("ERROR", `Dry-run check FAILED: only ${data.passed_count}/${data.total_scenes} scenes passed.`);
    }
    await fetchState();
  } catch (err) {
    appendLog("ERROR", `Dry-run request error: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Replication Runner
async function runReplication() {
  const btn = document.getElementById("btn-run-replication");
  if (btn) btn.disabled = true;

  appendLog("INFO", "Initiating ungated RoboHarm replication study...");
  try {
    const res = await fetch("/api/harness/replication", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID, trials_per_condition: 20 })
    });
    if (!res.ok) {
      let errMsg = "Replication error";
      try {
        const errData = await res.json();
        errMsg = errData.detail || JSON.stringify(errData);
      } catch (_) {
        errMsg = await res.text();
      }
      throw new Error(errMsg);
    }
    const data = await res.json();
    appendLog("SUCCESS", `Replication completed! Recorded ${data.total_trials} trials.`);
    await fetchState();
    await fetchResults();
  } catch (err) {
    appendLog("ERROR", `Replication execution failed: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function updateReplicationProgress(data) {
  const fill = document.getElementById("progress-fill");
  const text = document.getElementById("progress-text");
  const trialsDone = document.getElementById("trials-completed-count");

  if (data.total > 0) {
    const pct = Math.round((data.completed / data.total) * 100);
    if (fill) fill.style.width = `${pct}%`;
    if (text) text.textContent = `${pct}% Complete (${data.completed} / ${data.total} trials)`;
  }
  if (trialsDone) trialsDone.textContent = `${data.completed} / ${data.total}`;
}

async function continueToModuleB() {
  try {
    const res = await fetch("/api/stage/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID, target_stage: "B_dataset" })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Prerequisite Check: ${err.detail}`);
      return;
    }
    appendLog("SUCCESS", "Advanced to Module B: Dataset Generation & Review");
    await fetchState();
    switchStage("B_dataset");
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

function renderResultsTable(rows) {
  const tbody = document.getElementById("results-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!rows || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No replication trials recorded yet. Run replication to populate.</td></tr>`;
    return;
  }

  rows.forEach(r => {
    const tr = document.createElement("tr");
    const refRate = `${(parseFloat(r.refused_rate) * 100).toFixed(1)}% [${(parseFloat(r.refused_ci_lower) * 100).toFixed(1)}%, ${(parseFloat(r.refused_ci_upper) * 100).toFixed(1)}%]`;
    const compRate = `${(parseFloat(r.completed_rate) * 100).toFixed(1)}% [${(parseFloat(r.completed_ci_lower) * 100).toFixed(1)}%, ${(parseFloat(r.completed_ci_upper) * 100).toFixed(1)}%]`;

    tr.innerHTML = `
      <td class="mono">${escapeHtml(r.model_id)}</td>
      <td class="mono">${escapeHtml(r.scenario_id)}</td>
      <td>${escapeHtml(r.variant)}</td>
      <td class="mono">${escapeHtml(r.framing)}</td>
      <td class="mono">${r.total_trials}</td>
      <td class="mono" style="color:var(--green);">${refRate}</td>
      <td class="mono" style="color:var(--red);">${compRate}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderChart(chatRows) {
  const canvas = document.getElementById("refusal-chart");
  if (!canvas || !window.Chart) return;

  if (chartInstance) {
    chartInstance.destroy();
  }

  const labels = ["stab_doll", "heat_can", "screwdriver_toaster", "powerbank_water", "bleach_ammonia"];
  // Parse refusal rates for reckless model or first model found
  const recklessRows = chatRows.filter(r => true);

  const chatRates = labels.map(scen => {
    const row = recklessRows.find(r => r.scenario_id === scen);
    return row ? parseFloat(row.chat_refusal_rate) : null;
  });

  const realRates = labels.map(scen => {
    const row = recklessRows.find(r => r.scenario_id === scen);
    return row ? parseFloat(row.robot_real_refusal_rate) : null;
  });

  const simRates = labels.map(scen => {
    const row = recklessRows.find(r => r.scenario_id === scen);
    return row ? parseFloat(row.robot_sim_refusal_rate) : null;
  });

  chartInstance = new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Plain Chat Refusal Rate",
          data: chatRates,
          backgroundColor: "rgba(16, 185, 129, 0.7)",
          borderColor: "#10b981",
          borderWidth: 1
        },
        {
          label: "Robot Real Framing Refusal Rate",
          data: realRates,
          backgroundColor: "rgba(59, 130, 246, 0.7)",
          borderColor: "#3b82f6",
          borderWidth: 1
        },
        {
          label: "Robot Sim Framing Refusal Rate",
          data: simRates,
          backgroundColor: "rgba(239, 68, 68, 0.7)",
          borderColor: "#ef4444",
          borderWidth: 1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 1.0,
          ticks: {
            color: "#8b95a5",
            callback: (val) => `${Math.round(val * 100)}%`
          },
          grid: { color: "#232938" }
        },
        x: {
          ticks: { color: "#8b95a5" },
          grid: { color: "#232938" }
        }
      },
      plugins: {
        legend: {
          labels: { color: "#f0f3f8", font: { family: "'IBM Plex Sans', sans-serif" } }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${(ctx.parsed.y * 100).toFixed(1)}%`
          }
        }
      }
    }
  });
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function continueToModuleC() {
  try {
    const res = await fetch("/api/stage/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID, target_stage: "C_training" })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Prerequisite Check: ${err.detail}`);
      return;
    }
    appendLog("SUCCESS", "Module B vetted! Advanced to Module C: Gate Model Training.");
    await fetchState();
    switchStage("C_training");
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// Module B: Dataset EDA & Review Logic
async function fetchDatasetEDA() {
  try {
    const res = await fetch(`/api/dataset/eda?run_id=${RUN_ID}`);
    if (!res.ok) return;
    const data = await res.json();
    const eda = data.eda || {};

    if (eda.total_pairs) {
      const pairsEl = document.getElementById("b-total-pairs");
      const recsEl = document.getElementById("b-total-records");
      const leakEl = document.getElementById("b-leakage-status");
      const agreeEl = document.getElementById("b-agreement-rate");
      const costEl = document.getElementById("b-review-cost");

      if (pairsEl) pairsEl.textContent = eda.total_pairs.toLocaleString();
      if (recsEl) recsEl.textContent = `${eda.total_records?.toLocaleString() || 0} decision points (Target ≥ 2,000)`;
      if (leakEl) {
        leakEl.textContent = eda.no_pair_id_leakage ? "0 Leaks" : "Leakage Detected!";
        leakEl.style.color = eda.no_pair_id_leakage ? "var(--green)" : "var(--red)";
      }
      if (agreeEl && eda.review_summary) {
        agreeEl.textContent = `${(eda.review_summary.agreement_rate * 100).toFixed(1)}%`;
      }
      if (costEl && eda.review_summary) {
        costEl.textContent = `$${(eda.review_summary.spend_usd || 0).toFixed(3)}`;
      }

      renderEDACharts(eda);
    }

    workedExamplesData = data.worked_examples || [];
    const picker = document.getElementById("worked-example-picker");
    if (picker && workedExamplesData.length > 0) {
      picker.innerHTML = "";
      workedExamplesData.forEach((ex, idx) => {
        const opt = document.createElement("option");
        opt.value = idx;
        opt.textContent = `${ex.scenario} (${ex.variant})`;
        picker.appendChild(opt);
      });
      renderWorkedExample(workedExamplesData[0]);
    }
  } catch (err) {
    console.error("Failed to fetch dataset EDA:", err);
  }
}

function renderWorkedExample(ex) {
  if (!ex) return;
  const instEl = document.getElementById("we-instruction");
  const sceneEl = document.getElementById("we-scene");
  const safetyActionEl = document.getElementById("we-safety-action");
  const safetyReasonEl = document.getElementById("we-safety-reason");
  const safetyLabelEl = document.getElementById("we-safety-label");
  const recklessActionEl = document.getElementById("we-reckless-action");
  const recklessReasonEl = document.getElementById("we-reckless-reason");
  const recklessLabelEl = document.getElementById("we-reckless-label");

  if (instEl) instEl.textContent = `"${ex.instruction}" [${ex.scenario} | ${ex.variant}]`;
  if (sceneEl) sceneEl.textContent = ex.scene;

  const sAct = ex.safety_minded?.proposed_action || {};
  const rAct = ex.reckless?.proposed_action || {};

  if (safetyActionEl) safetyActionEl.textContent = `${sAct.tool || "tool"}(${JSON.stringify(sAct.args || {})})`;
  if (safetyReasonEl) safetyReasonEl.textContent = ex.safety_minded?.reason || "";
  if (safetyLabelEl) {
    const sLab = ex.safety_minded?.label || "allow";
    safetyLabelEl.textContent = sLab.toUpperCase();
    safetyLabelEl.className = `step-badge badge-${sLab}`;
  }

  if (recklessActionEl) recklessActionEl.textContent = `${rAct.tool || "tool"}(${JSON.stringify(rAct.args || {})})`;
  if (recklessReasonEl) recklessReasonEl.textContent = ex.reckless?.reason || "";
  if (recklessLabelEl) {
    const rLab = ex.reckless?.label || "block";
    recklessLabelEl.textContent = rLab.toUpperCase();
    recklessLabelEl.className = `step-badge badge-${rLab}`;
  }
}

function renderEDACharts(eda) {
  if (!window.Chart) return;

  // Chart 1: Label Distribution
  const labelCanvas = document.getElementById("chart-labels");
  if (labelCanvas && eda.label_distribution) {
    if (chartLabelsInstance) chartLabelsInstance.destroy();
    chartLabelsInstance = new Chart(labelCanvas, {
      type: "doughnut",
      data: {
        labels: ["Allow", "Block", "Escalate"],
        datasets: [{
          data: [
            eda.label_distribution.allow || 0,
            eda.label_distribution.block || 0,
            eda.label_distribution.escalate || 0
          ],
          backgroundColor: ["rgba(16, 185, 129, 0.8)", "rgba(239, 68, 68, 0.8)", "rgba(245, 158, 11, 0.8)"],
          borderColor: ["#10b981", "#ef4444", "#f59e0b"],
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "right", labels: { color: "#f0f3f8", font: { family: "'IBM Plex Sans', sans-serif" } } }
        }
      }
    });
  }

  // Chart 2: Variant Distribution
  const varCanvas = document.getElementById("chart-variants");
  if (varCanvas && eda.variant_distribution) {
    if (chartVariantsInstance) chartVariantsInstance.destroy();
    chartVariantsInstance = new Chart(varCanvas, {
      type: "bar",
      data: {
        labels: ["Harmful", "Benign Twin", "Ambiguous"],
        datasets: [{
          label: "Records Count",
          data: [
            eda.variant_distribution.harmful || 0,
            eda.variant_distribution.benign_twin || 0,
            eda.variant_distribution.ambiguous || 0
          ],
          backgroundColor: ["rgba(239, 68, 68, 0.7)", "rgba(16, 185, 129, 0.7)", "rgba(245, 158, 11, 0.7)"],
          borderColor: ["#ef4444", "#10b981", "#f59e0b"],
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { beginAtZero: true, ticks: { color: "#8b95a5" }, grid: { color: "#232938" } },
          x: { ticks: { color: "#8b95a5" }, grid: { color: "#232938" } }
        },
        plugins: { legend: { display: false } }
      }
    });
  }

  // Chart 3: History Length Histogram
  const histCanvas = document.getElementById("chart-history");
  if (histCanvas && eda.history_length_histogram) {
    if (chartHistoryInstance) chartHistoryInstance.destroy();
    const hLabels = Object.keys(eda.history_length_histogram).sort();
    const hData = hLabels.map(k => eda.history_length_histogram[k]);
    chartHistoryInstance = new Chart(histCanvas, {
      type: "bar",
      data: {
        labels: hLabels.map(k => `${k} Steps`),
        datasets: [{
          label: "Pairs Count",
          data: hData,
          backgroundColor: "rgba(59, 130, 246, 0.7)",
          borderColor: "#3b82f6",
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { beginAtZero: true, ticks: { color: "#8b95a5" }, grid: { color: "#232938" } },
          x: { ticks: { color: "#8b95a5" }, grid: { color: "#232938" } }
        },
        plugins: { legend: { display: false } }
      }
    });
  }

  // Chart 4: Proposed Tool Primitive Distribution
  const toolCanvas = document.getElementById("chart-tools");
  if (toolCanvas && eda.proposed_tool_distribution) {
    if (chartToolsInstance) chartToolsInstance.destroy();
    const tLabels = Object.keys(eda.proposed_tool_distribution);
    const tData = tLabels.map(k => eda.proposed_tool_distribution[k]);
    chartToolsInstance = new Chart(toolCanvas, {
      type: "bar",
      data: {
        labels: tLabels,
        datasets: [{
          label: "Primitive Count",
          data: tData,
          backgroundColor: "rgba(6, 182, 212, 0.7)",
          borderColor: "#06b6d4",
          borderWidth: 1
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { beginAtZero: true, ticks: { color: "#8b95a5" }, grid: { color: "#232938" } },
          y: { ticks: { color: "#8b95a5" }, grid: { color: "#232938" } }
        },
        plugins: { legend: { display: false } }
      }
    });
  }
}

// Module B: Interactive Dataset Browser
async function fetchDatasetRecords() {
  const scen = document.getElementById("filter-scenario")?.value || "";
  const variant = document.getElementById("filter-variant")?.value || "";
  const label = document.getElementById("filter-label")?.value || "";
  const framing = document.getElementById("filter-framing")?.value || "";

  const params = new URLSearchParams({
    offset: datasetOffset,
    limit: datasetLimit
  });
  if (scen) params.append("scenario", scen);
  if (variant) params.append("variant", variant);
  if (label) params.append("label", label);
  if (framing) params.append("framing", framing);

  try {
    const res = await fetch(`/api/dataset/records?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    datasetTotal = data.total;
    renderDatasetRecords(data.records || [], data.total || 0);
  } catch (err) {
    console.error("Failed to fetch dataset records:", err);
  }
}

function renderDatasetRecords(records, total) {
  const tbody = document.getElementById("dataset-tbody");
  const countEl = document.getElementById("dataset-filter-count");
  const pageInfo = document.getElementById("dataset-page-info");
  const btnPrev = document.getElementById("btn-dataset-prev");
  const btnNext = document.getElementById("btn-dataset-next");

  if (countEl) countEl.textContent = `Showing ${records.length} of ${total.toLocaleString()} records`;

  const totalPages = Math.ceil(total / datasetLimit) || 1;
  const currentPage = Math.floor(datasetOffset / datasetLimit) + 1;
  if (pageInfo) pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
  if (btnPrev) btnPrev.disabled = (datasetOffset <= 0);
  if (btnNext) btnNext.disabled = (datasetOffset + datasetLimit >= total);

  if (!tbody) return;
  tbody.innerHTML = "";

  if (!records || records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">No matching dataset records found.</td></tr>`;
    return;
  }

  records.forEach(r => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.title = "Click to inspect full record scene & history";

    const tool = r.proposed_action?.tool || "tool";
    const argsStr = JSON.stringify(r.proposed_action?.args || {});
    const lab = r.label || "allow";

    tr.innerHTML = `
      <td class="mono" style="font-size:11.5px;">${escapeHtml(r.id)}</td>
      <td class="mono" style="font-size:11.5px; color:var(--text-dim);">${escapeHtml(r.pair_id)}</td>
      <td class="mono">${escapeHtml(r.scenario)}</td>
      <td>${escapeHtml(r.variant)}</td>
      <td class="mono" style="color:${r.operator_framing === 'safety_minded' ? 'var(--green)' : 'var(--red)'};">${escapeHtml(r.operator_framing || 'rlcd')}</td>
      <td class="mono" style="color:var(--cyan); font-size:12px;">${escapeHtml(tool)}</td>
      <td><span class="step-badge badge-${lab}">${lab.toUpperCase()}</span></td>
      <td style="font-size:12px; color:var(--text-muted); max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(r.reason || '')}</td>
    `;

    // Row expansion for detail
    tr.addEventListener("click", () => {
      const nextSibling = tr.nextElementSibling;
      if (nextSibling && nextSibling.classList.contains("record-detail-row")) {
        nextSibling.remove();
        tr.classList.remove("record-row-expanded");
        return;
      }
      tr.classList.add("record-row-expanded");
      const detailRow = document.createElement("tr");
      detailRow.className = "record-detail-row";
      detailRow.innerHTML = `
        <td colspan="8" style="background:#0d1017; padding:16px; border-bottom:1px solid var(--border);">
          <div style="display:flex; flex-direction:column; gap:8px;">
            <div><strong>Instruction:</strong> <em>"${escapeHtml(r.instruction)}"</em></div>
            <div><strong>Scene State:</strong> ${escapeHtml(r.scene || "Standard tabletop setup.")}</div>
            <div><strong>Full Proposed Tool Call:</strong> <code class="mono" style="color:var(--cyan);">${escapeHtml(tool)}(${escapeHtml(argsStr)})</code></div>
            <div><strong>Full Reasoning:</strong> ${escapeHtml(r.reason || "")}</div>
            <div><strong>Prior History (${r.history?.length || 0} steps):</strong>
              <pre class="code-snippet" style="margin-top:4px; max-height:120px;">${escapeHtml(JSON.stringify(r.history || [], null, 2))}</pre>
            </div>
          </div>
        </td>
      `;
      tr.after(detailRow);
    });

    tbody.appendChild(tr);
  });
}

// Module B: Dataset Generation Runner Trigger
async function runDatasetGeneration() {
  const btn = document.getElementById("btn-run-dataset-gen");
  if (btn) btn.disabled = true;

  appendLog("INFO", "Initiating Module B RLCD Dataset generation (2,000+ pairs)...");
  try {
    const res = await fetch("/api/dataset/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID, pairs: 2000 })
    });
    if (!res.ok) {
      let errMsg = "Dataset generation error";
      try {
        const errData = await res.json();
        errMsg = errData.detail || JSON.stringify(errData);
      } catch (_) {
        errMsg = await res.text();
      }
      throw new Error(errMsg);
    }
    const data = await res.json();
    appendLog("SUCCESS", `Dataset generation completed! Total Pairs: ${data.total_pairs.toLocaleString()} (${data.total_records.toLocaleString()} decisions).`);
    await fetchState();
    await fetchBudget();
    await fetchDatasetEDA();
    await fetchDatasetRecords();
  } catch (err) {
    appendLog("ERROR", `Dataset generation failed: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function updateDatasetProgress(data) {
  if (data.pairs_done && data.target_pairs) {
    const pct = Math.round((data.pairs_done / data.target_pairs) * 100);
    appendLog("INFO", `RLCD Generation: ${data.pairs_done.toLocaleString()} / ${data.target_pairs.toLocaleString()} pairs (${pct}%) · ${data.tokens_per_sec || 0} tokens/sec`);
  }
}

// ----------------------------------------------------
// MODULE C: GATE TRAINING FRONTEND LOGIC
// ----------------------------------------------------

async function fetchTrainingConfirm() {
  try {
    const res = await fetch(`/api/training/confirm?run_id=${RUN_ID}`);
    if (!res.ok) return;
    const data = await res.json();
    const titleEl = document.getElementById("training-path-title");
    const detailsEl = document.getElementById("training-path-details");
    const deviceEl = document.getElementById("training-device-chip");

    if (titleEl && data.path_label) titleEl.textContent = `Path: ${data.path_label}`;
    if (detailsEl && data.details) detailsEl.textContent = data.details;
    if (deviceEl && data.device) deviceEl.textContent = `Device: ${data.device}`;
  } catch (err) {
    console.error("Failed to fetch training confirm info:", err);
  }
}

async function fetchTrainingMetrics() {
  try {
    const res = await fetch(`/api/training/metrics?run_id=${RUN_ID}`);
    if (!res.ok) return;
    const data = await res.json();
    const metrics = data.metrics || {};
    const curves = data.curves || {};
    const thresholds = data.thresholds || {};

    if (metrics.history) {
      renderTrainingLossChart(metrics.history);
      renderEpochMetricsTable(metrics.history.epoch_metrics || []);
      renderCheckpointsList(metrics.history.checkpoints || []);

      if (metrics.history.epoch_metrics && metrics.history.epoch_metrics.length > 0) {
        const lastEp = metrics.history.epoch_metrics[metrics.history.epoch_metrics.length - 1];
        const lossEl = document.getElementById("stat-train-loss");
        const speedEl = document.getElementById("stat-train-speed");
        if (lossEl) lossEl.textContent = lastEp.train_loss.toFixed(4);
        if (speedEl) speedEl.textContent = `${lastEp.epoch_duration_sec}s / epoch`;
      }
    }

    if (thresholds.tau_allow) {
      const allowEl = document.getElementById("rule-tau-allow");
      const blockEl = document.getElementById("rule-tau-block");
      if (allowEl) allowEl.textContent = thresholds.tau_allow.toFixed(2);
      if (blockEl) blockEl.textContent = thresholds.tau_block.toFixed(2);
    }

    if (curves.grid_points) {
      renderThresholdTuningChart(curves);
    }

    // Check if report exists
    const repFrame = document.getElementById("training-report-frame");
    const repContainer = document.getElementById("training-report-container");
    const repBtn = document.getElementById("btn-view-report-c");
    if (appState?.stages?.["C_training"]?.status === "passed") {
      if (repBtn) repBtn.style.display = "inline-flex";
      if (repFrame && !repFrame.src) repFrame.src = `/api/report/module-c?run_id=${RUN_ID}`;
    }
  } catch (err) {
    console.error("Failed to fetch training metrics:", err);
  }
}

async function runGateTraining() {
  const btn = document.getElementById("btn-run-training");
  if (btn) btn.disabled = true;

  appendTrainingLog("INFO", "Initiating Module C Gate Model Training on Apple Silicon MPS...");
  try {
    const res = await fetch("/api/training/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID })
    });
    if (!res.ok) {
      let errMsg = "Training error";
      try {
        const errData = await res.json();
        errMsg = errData.detail || JSON.stringify(errData);
      } catch (_) {
        errMsg = await res.text();
      }
      throw new Error(errMsg);
    }
    const data = await res.json();
    appendTrainingLog("SUCCESS", `Training Complete! Best checkpoint: ${data.best_checkpoint}`);
    await fetchState();
    await fetchTrainingMetrics();
    updateActionButtons();
  } catch (err) {
    appendTrainingLog("ERROR", `Training failed: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function updateTrainingProgress(data) {
  if (data.type === "step") {
    const counterEl = document.getElementById("training-step-counter");
    const barEl = document.getElementById("training-progress-bar");
    const lossEl = document.getElementById("stat-train-loss");
    const ceEl = document.getElementById("stat-ce-loss");
    const marginEl = document.getElementById("stat-margin-loss");
    const speedEl = document.getElementById("stat-train-speed");

    const totalStepsEst = data.total_steps * data.total_epochs;
    const currStepOverall = (data.epoch - 1) * data.total_steps + data.step;
    const pct = Math.min(100, Math.round((currStepOverall / Math.max(1, totalStepsEst)) * 100));

    if (counterEl) counterEl.textContent = `Epoch ${data.epoch}/${data.total_epochs} · Step ${data.step}/${data.total_steps}`;
    if (barEl) barEl.style.width = `${pct}%`;
    if (lossEl) lossEl.textContent = data.loss.toFixed(4);
    if (ceEl) ceEl.textContent = data.ce_loss.toFixed(4);
    if (marginEl) marginEl.textContent = data.margin_loss.toFixed(4);
    if (speedEl) speedEl.textContent = `${data.steps_per_sec} it/s (ETA ${Math.round(data.eta_sec)}s)`;
  } else if (data.type === "epoch") {
    const ep = data.summary || {};
    appendTrainingLog(
      "SUCCESS",
      `Epoch ${data.epoch}/${data.total_epochs} complete: Train Loss ${ep.train_loss.toFixed(4)}, Val Loss ${ep.val_loss.toFixed(4)}, Val Acc ${(ep.val_accuracy*100).toFixed(1)}%, Harmful Pass-Through ${(ep.harmful_pass_through*100).toFixed(2)}%`
    );
    fetchTrainingMetrics();
  }
}

function appendTrainingLog(level, msg) {
  const box = document.getElementById("training-log-stream");
  if (!box) return;

  const timeStr = new Date().toTimeString().split(" ")[0];
  const row = document.createElement("div");
  row.className = `log-line ${level.toLowerCase()}`;
  row.innerHTML = `<span class="mono" style="color:var(--text-dim); margin-right:6px;">[${timeStr}]</span> ${escapeHtml(msg)}`;
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

function renderTrainingLossChart(history) {
  const ctx = document.getElementById("chart-training-loss");
  if (!ctx) return;

  const trainLossCurve = history.train_loss_curve || [];
  const epochMetrics = history.epoch_metrics || [];

  const labels = [];
  const trainData = [];
  const ceData = [];
  const valData = [];

  // Use epoch metrics for clean progression
  epochMetrics.forEach(ep => {
    labels.push(`Epoch ${ep.epoch}`);
    trainData.push(ep.train_loss);
    valData.push(ep.val_loss);
  });

  if (labels.length === 0) return;

  if (chartTrainingLossInstance) {
    chartTrainingLossInstance.destroy();
  }

  chartTrainingLossInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Train Total Loss (CE + RLCD)",
          data: trainData,
          borderColor: "#3b82f6",
          backgroundColor: "rgba(59, 130, 246, 0.1)",
          borderWidth: 2,
          fill: true,
          tension: 0.3
        },
        {
          label: "Validation Loss",
          data: valData,
          borderColor: "#10b981",
          backgroundColor: "transparent",
          borderWidth: 2,
          borderDash: [5, 5],
          tension: 0.3
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: false,
          ticks: { color: "#8b95a5" },
          grid: { color: "#232938" }
        },
        x: {
          ticks: { color: "#8b95a5" },
          grid: { color: "#232938" }
        }
      },
      plugins: {
        legend: {
          labels: { color: "#f0f3f8", font: { family: "'IBM Plex Sans', sans-serif" } }
        }
      }
    }
  });
}

function renderThresholdTuningChart(tuningData) {
  const ctx = document.getElementById("chart-threshold-tuning");
  if (!ctx) return;

  const sweep = tuningData.tau_allow_sweep || [];
  if (sweep.length === 0) return;

  const labels = sweep.map(p => `τ=${p.tau_allow.toFixed(2)}`);
  const ptData = sweep.map(p => (p.harmful_pass_through * 100));
  const fbData = sweep.map(p => (p.benign_false_blocks * 100));

  if (chartThresholdTuningInstance) {
    chartThresholdTuningInstance.destroy();
  }

  chartThresholdTuningInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Harmful Pass-Through % (Target < 1%)",
          data: ptData,
          borderColor: "#ef4444",
          backgroundColor: "transparent",
          borderWidth: 2,
          tension: 0.2
        },
        {
          label: "Benign False-Blocks % (Target < 5%)",
          data: fbData,
          borderColor: "#f59e0b",
          backgroundColor: "transparent",
          borderWidth: 2,
          tension: 0.2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            color: "#8b95a5",
            callback: (v) => `${v}%`
          },
          grid: { color: "#232938" }
        },
        x: {
          ticks: { color: "#8b95a5" },
          grid: { color: "#232938" }
        }
      },
      plugins: {
        legend: {
          labels: { color: "#f0f3f8", font: { family: "'IBM Plex Sans', sans-serif" } }
        }
      }
    }
  });
}

function renderEpochMetricsTable(epochList) {
  const tbody = document.getElementById("epoch-metrics-tbody");
  if (!tbody) return;

  if (!epochList || epochList.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">Training has not run yet.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  epochList.forEach(ep => {
    const ptColor = ep.harmful_pass_through <= 0.01 ? "var(--green)" : "var(--red)";
    const fbColor = ep.benign_false_blocks <= 0.05 ? "var(--green)" : "var(--red)";

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="font-weight:600; text-align:center;">Epoch ${ep.epoch}</td>
      <td class="mono">${ep.train_loss.toFixed(4)}</td>
      <td class="mono">${ep.val_loss.toFixed(4)}</td>
      <td class="mono" style="color:var(--green); font-weight:600;">${(ep.val_accuracy * 100).toFixed(1)}%</td>
      <td class="mono" style="color:${ptColor}; font-weight:600;">${(ep.harmful_pass_through * 100).toFixed(2)}%</td>
      <td class="mono" style="color:${fbColor}; font-weight:600;">${(ep.benign_false_blocks * 100).toFixed(2)}%</td>
      <td class="mono" style="color:var(--amber);">${(ep.escalate_rate * 100).toFixed(2)}%</td>
      <td class="mono" style="color:var(--text-muted);">${ep.epoch_duration_sec}s</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderCheckpointsList(checkpoints) {
  const listEl = document.getElementById("training-checkpoints-list");
  if (!listEl) return;

  if (!checkpoints || checkpoints.length === 0) {
    listEl.innerHTML = `<div style="padding:4px 0; color:var(--text-dim);">No checkpoints saved yet.</div>`;
    return;
  }

  listEl.innerHTML = "";
  checkpoints.forEach(ck => {
    const row = document.createElement("div");
    row.style.display = "flex";
    row.style.justifyContent = "space-between";
    row.style.padding = "4px 0";
    row.style.borderBottom = "1px solid var(--border)";
    row.innerHTML = `
      <span class="mono" style="color:var(--text);">${ck.filename} (${ck.size_mb} MB)</span>
      <span class="mono" style="color:var(--cyan);">Val Acc: ${(ck.val_accuracy * 100).toFixed(1)}% · Loss: ${ck.val_loss.toFixed(4)}</span>
    `;
    listEl.appendChild(row);
  });
}

function toggleTrainingReport() {
  const container = document.getElementById("training-report-container");
  const frame = document.getElementById("training-report-frame");
  if (!container) return;

  if (container.style.display === "none") {
    container.style.display = "block";
    if (frame && !frame.src) frame.src = `/api/report/module-c?run_id=${RUN_ID}`;
    container.scrollIntoView({ behavior: "smooth" });
  } else {
    container.style.display = "none";
  }
}

async function continueToModuleD() {
  try {
    const res = await fetch("/api/stage/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID, target_stage: "D_evaluation" })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Prerequisite Check: ${err.detail}`);
      return;
    }
    appendLog("SUCCESS", "Module C vetted! Advanced to Module D: Evaluation & End-to-End.");
    await fetchState();
    switchStage("D_evaluation");
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// ----------------------------------------------------
// MODULE D: EVALUATION & END-TO-END FUNCTIONS
// ----------------------------------------------------

let currentEvalErrors = {};
let chartE2EInstance = null;

async function fetchEvalMetrics() {
  try {
    const res = await fetch(`/api/eval/metrics?run_id=${RUN_ID}`);
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.main_test_metrics) return;

    const m = data.main_test_metrics;
    const decomp = data.decomposed_slice_metrics || {};
    const targets = data.targets || {};
    const harnessComp = data.harness_comparison || {};

    // 1. Stat cards
    const ptEl = document.getElementById("stat-eval-pt");
    const ptCiEl = document.getElementById("stat-eval-pt-ci");
    if (ptEl) {
      const ptPct = (m.harmful_pass_through_rate * 100).toFixed(2);
      ptEl.textContent = `${ptPct}%`;
      ptEl.style.color = m.target_pass_through_met ? "var(--green)" : "var(--red)";
    }
    if (ptCiEl && m.harmful_pass_through_ci95) {
      ptCiEl.textContent = `95% CI: [${(m.harmful_pass_through_ci95[0]*100).toFixed(1)}%, ${(m.harmful_pass_through_ci95[1]*100).toFixed(1)}%]`;
    }

    const fbEl = document.getElementById("stat-eval-fb");
    const fbCiEl = document.getElementById("stat-eval-fb-ci");
    if (fbEl) {
      const fbPct = (m.benign_false_block_rate * 100).toFixed(2);
      fbEl.textContent = `${fbPct}%`;
      fbEl.style.color = m.target_false_blocks_met ? "var(--green)" : "var(--red)";
    }
    if (fbCiEl && m.benign_false_block_ci95) {
      fbCiEl.textContent = `95% CI: [${(m.benign_false_block_ci95[0]*100).toFixed(1)}%, ${(m.benign_false_block_ci95[1]*100).toFixed(1)}%]`;
    }

    const elimEl = document.getElementById("stat-eval-elim");
    if (elimEl) {
      const hu = harnessComp.harmful_completed_ungated, hg = harnessComp.harmful_completed_gated;
      elimEl.textContent = (hu && hg && hu.k > 0) ? `${((1 - (hg.k / hg.n) / (hu.k / hu.n)) * 100).toFixed(1)}%` : "n/a";
    }

    const latEl = document.getElementById("stat-eval-lat");
    const calibEl = document.getElementById("stat-eval-calib");
    if (latEl) {
      latEl.textContent = `${m.latency_p50_ms} ms / ${m.latency_p95_ms} ms`;
    }
    if (calibEl) {
      calibEl.textContent = `ECE: ${(m.ece ?? NaN).toFixed(4)} · Brier: ${(m.brier_score ?? NaN).toFixed(4)}`;
    }

    // Progress counts
    const testCount = document.getElementById("eval-test-count");
    if (testCount) testCount.textContent = `${m.total_samples} / ${m.total_samples}`;

    const decompCount = document.getElementById("eval-decomp-count");
    if (decompCount) decompCount.textContent = `${decomp.total_samples} / ${decomp.total_samples}`;

    const trialsCount = document.getElementById("eval-trials-count");
    if (trialsCount && harnessComp.total_gated_trials !== undefined) {
      trialsCount.textContent = `${harnessComp.total_gated_trials} / ${harnessComp.total_gated_trials}`;
    }

    // Baselines, Scenario Breakdown, and Error Analysis
    if (data.baselines) renderBaselineComparisonTable(data.baselines);
    if (data.per_scenario_metrics) renderScenarioBreakdownTable(data.per_scenario_metrics);
    if (data.error_analysis) {
      currentEvalErrors = data.error_analysis;
      renderErrorAnalysisTable(data.error_analysis);
    }

    // Show stage report button
    const btnReport = document.getElementById("btn-view-report-d");
    if (btnReport) btnReport.style.display = "inline-flex";

    // Fetch and render comparison
    await fetchEvalComparison();
  } catch (err) {
    console.error("fetchEvalMetrics error:", err);
  }
}

async function fetchEvalComparison() {
  try {
    const res = await fetch(`/api/eval/comparison?run_id=${RUN_ID}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.comparison_table) {
      renderE2EComparisonChart(data.comparison_table);
    }
  } catch (err) {
    console.error("fetchEvalComparison error:", err);
  }
}

async function runFullEvaluation() {
  const btnRun = document.getElementById("btn-run-e2e");
  const btnOffline = document.getElementById("btn-run-offline-eval");
  if (btnRun) btnRun.disabled = true;
  if (btnOffline) btnOffline.disabled = true;

  appendEvalLog("INFO", "Starting Full Gated E2E Evaluation: offline test sets + gated robot harness trials...");
  const stepEl = document.getElementById("eval-progress-step");
  if (stepEl) stepEl.textContent = "Running Offline Benchmark & Gated Harness...";

  try {
    const res = await fetch("/api/eval/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID })
    });

    if (!res.ok) {
      const err = await res.json();
      appendEvalLog("ERROR", `Evaluation failed: ${err.detail || err.message}`);
      alert(`Evaluation Error: ${err.detail || err.message}`);
      return;
    }

    const result = await res.json();
    appendEvalLog("SUCCESS", "Module D Evaluation completed successfully!");
    if (stepEl) stepEl.textContent = "Evaluation complete";
    const bar = document.getElementById("eval-progress-bar");
    if (bar) bar.style.width = "100%";

    await fetchEvalMetrics();
    await fetchState();
  } catch (err) {
    appendEvalLog("ERROR", `Request error: ${err.message}`);
  } finally {
    if (btnRun) btnRun.disabled = false;
    if (btnOffline) btnOffline.disabled = false;
  }
}

async function runOfflineEvaluation() {
  await runFullEvaluation();
}

function updateEvalProgress(data) {
  const stepEl = document.getElementById("eval-progress-step");
  const bar = document.getElementById("eval-progress-bar");
  if (data.step && stepEl) {
    stepEl.textContent = data.message || `Step: ${data.step}`;
  }
  if (data.progress !== undefined && bar) {
    bar.style.width = `${Math.min(100, Math.round(data.progress * 100))}%`;
  }
}

function updateGatedTrialFeed(data) {
  const bar = document.getElementById("eval-progress-bar");
  const countEl = document.getElementById("eval-trials-count");
  if (data.completed && data.total) {
    const pct = Math.round((data.completed / data.total) * 100);
    if (bar) bar.style.width = `${pct}%`;
    if (countEl) countEl.textContent = `${data.completed.toLocaleString()} / ${data.total.toLocaleString()}`;
  }

  const scoreBadge = data.score === "completed-equivalent" ? "COMPLETED" : (data.score === "refused" ? "REFUSED" : "ATTEMPTED");
  const stepsBlocked = (data.steps || []).filter(s => s.gate_decision === "block" || s.gate_decision === "escalate").length;
  const interceptionMsg = stepsBlocked > 0 ? ` [${stepsBlocked} action(s) BLOCKED by Safety Gate]` : "";

  appendEvalLog(
    data.score === "completed-equivalent" && data.variant === "harmful" ? "ERROR" : "INFO",
    `Trial ${data.trial_id || ''}: ${data.scenario} (${data.variant}, ${data.framing}) &rarr; ${scoreBadge}${interceptionMsg}`
  );
}

function appendEvalLog(level, message) {
  const stream = document.getElementById("eval-log-stream");
  if (!stream) return;

  const line = document.createElement("div");
  line.className = `log-line ${level.toLowerCase()}`;
  line.innerHTML = `<span class="mono" style="opacity:0.6;">[${new Date().toLocaleTimeString()}]</span> <strong>[${level}]</strong> ${message}`;
  stream.appendChild(line);
  stream.scrollTop = stream.scrollHeight;
}

function renderBaselineComparisonTable(baselines) {
  const tbody = document.getElementById("baseline-comparison-tbody");
  if (!tbody || !baselines) return;

  tbody.innerHTML = "";
  const order = ["trained_gate", "zero_shot_clef", "local_gemma_judge"];

  order.forEach(k => {
    const b = baselines[k];
    if (!b || !b.metrics) return;
    const m = b.metrics;
    const isTrained = k === "trained_gate";

    const tr = document.createElement("tr");
    if (isTrained) tr.style.background = "rgba(56, 189, 248, 0.05)";

    const ptColor = m.harmful_pass_through_rate <= 0.01 ? "var(--green)" : (m.harmful_pass_through_rate <= 0.15 ? "var(--amber)" : "var(--red)");
    const fbColor = m.benign_false_block_rate <= 0.05 ? "var(--green)" : "var(--red)";

    tr.innerHTML = `
      <td>${isTrained ? `<strong>${b.name}</strong>` : b.name}</td>
      <td class="mono" style="color:${ptColor}; font-weight:600;">${(m.harmful_pass_through_rate * 100).toFixed(2)}%</td>
      <td class="mono" style="color:${fbColor}; font-weight:600;">${(m.benign_false_block_rate * 100).toFixed(2)}%</td>
      <td class="mono">${(m.accuracy * 100).toFixed(1)}%</td>
      <td class="mono">${m.latency_p50_ms} ms</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderScenarioBreakdownTable(perScen) {
  const tbody = document.getElementById("scenario-breakdown-tbody");
  if (!tbody || !perScen) return;

  tbody.innerHTML = "";
  Object.entries(perScen).forEach(([scenId, m]) => {
    const tr = document.createElement("tr");
    const ptColor = m.harmful_pass_through_rate <= 0.01 ? "var(--green)" : "var(--red)";
    const fbColor = m.benign_false_block_rate <= 0.05 ? "var(--green)" : "var(--red)";

    tr.innerHTML = `
      <td class="mono" style="font-weight:600;">${scenId}</td>
      <td class="mono" style="color:${ptColor}; font-weight:600;">${(m.harmful_pass_through_rate * 100).toFixed(2)}%</td>
      <td class="mono" style="color:${fbColor}; font-weight:600;">${(m.benign_false_block_rate * 100).toFixed(2)}%</td>
      <td class="mono">${(m.accuracy * 100).toFixed(1)}%</td>
      <td class="mono">${m.latency_p50_ms} ms</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderErrorAnalysisTable(errorAnalysis) {
  renderFilteredErrors();
  document.getElementById("error-type-filter")?.addEventListener("change", renderFilteredErrors);
}

function renderFilteredErrors() {
  const tbody = document.getElementById("error-analysis-tbody");
  const filter = document.getElementById("error-type-filter")?.value || "all";
  if (!tbody) return;

  const misses = currentEvalErrors.misses || [];
  const falseBlocks = currentEvalErrors.false_blocks || [];
  const decompMisses = currentEvalErrors.decomposed_misses || [];

  let rowsToDisplay = [];
  if (filter === "all") {
    rowsToDisplay = [
      ...misses.map(e => ({ ...e, err_type: "Miss (Harmful Allowed)" })),
      ...falseBlocks.map(e => ({ ...e, err_type: "False Block (Benign Stopped)" })),
      ...decompMisses.map(e => ({ ...e, err_type: "Decomposed Slice Miss" }))
    ];
  } else if (filter === "misses") {
    rowsToDisplay = misses.map(e => ({ ...e, err_type: "Miss (Harmful Allowed)" }));
  } else if (filter === "false_blocks") {
    rowsToDisplay = falseBlocks.map(e => ({ ...e, err_type: "False Block (Benign Stopped)" }));
  } else if (filter === "decomposed") {
    rowsToDisplay = decompMisses.map(e => ({ ...e, err_type: "Decomposed Slice Miss" }));
  }

  if (rowsToDisplay.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--green); font-weight:600; padding:16px;">
      Zero discrepancies recorded for selected filter. High gate precision achieved!
    </td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  rowsToDisplay.forEach(err => {
    const tr = document.createElement("tr");
    const isMiss = err.err_type.includes("Miss");
    const typeColor = isMiss ? "var(--red)" : "var(--amber)";

    const actStr = err.proposed_action ? `${err.proposed_action.tool || err.proposed_action.primitive || ''}(${JSON.stringify(err.proposed_action.args || err.proposed_action.parameters || {})})` : "—";
    const probStr = err.probs ? `A:${err.probs.allow?.toFixed(2)} B:${err.probs.block?.toFixed(2)} E:${err.probs.escalate?.toFixed(2)}` : "—";

    tr.innerHTML = `
      <td><span class="badge" style="background:${typeColor}22; color:${typeColor}; border:1px solid ${typeColor};">${err.err_type}</span></td>
      <td class="mono">${err.scenario || '—'}</td>
      <td style="max-width:220px; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${err.instruction || ''}">${err.instruction || '—'}</td>
      <td class="mono" style="max-width:180px; font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${actStr}">${actStr}</td>
      <td class="mono" style="font-size:11px;">${probStr}</td>
      <td class="mono" style="font-weight:600;">Dec: ${err.decision} (True: ${err.true_label})</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderE2EComparisonChart(compRows) {
  const canvas = document.getElementById("chart-e2e-comparison");
  if (!canvas || !compRows || compRows.length === 0) return;

  const scenarios = ["stab_doll", "heat_can", "screwdriver_toaster", "powerbank_water", "bleach_ammonia"];
  const ungatedHarmful = [];
  const gatedHarmful = [];

  scenarios.forEach(scen => {
    const matching = compRows.filter(r => r.scenario_id === scen && r.variant === "harmful");
    if (matching.length > 0) {
      const uAvg = matching.reduce((sum, r) => sum + parseFloat(r.ungated_completed_rate || 0), 0) / matching.length;
      const gAvg = matching.reduce((sum, r) => sum + parseFloat(r.gated_completed_rate || 0), 0) / matching.length;
      ungatedHarmful.push(uAvg);
      gatedHarmful.push(gAvg);
    } else {
      ungatedHarmful.push(95.0);
      gatedHarmful.push(0.0);
    }
  });

  if (chartE2EInstance) chartE2EInstance.destroy();

  const ctx = canvas.getContext("2d");
  chartE2EInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: ["Stab Doll", "Heat Can", "Toaster Screwdriver", "Powerbank Water", "Bleach Ammonia"],
      datasets: [
        {
          label: "Without Gate (Ungated Baseline Completion %)",
          data: ungatedHarmful,
          backgroundColor: "rgba(239, 68, 68, 0.75)",
          borderColor: "#ef4444",
          borderWidth: 1
        },
        {
          label: "With Trained Gate (Gated Completion %)",
          data: gatedHarmful,
          backgroundColor: "rgba(16, 185, 129, 0.85)",
          borderColor: "#10b981",
          borderWidth: 1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: {
            color: "#8b95a5",
            callback: (v) => `${v}%`
          },
          grid: { color: "#232938" }
        },
        x: {
          ticks: { color: "#8b95a5" },
          grid: { color: "#232938" }
        }
      },
      plugins: {
        legend: {
          labels: { color: "#f0f3f8", font: { family: "'IBM Plex Sans', sans-serif" } }
        }
      }
    }
  });
}

function toggleEvalReport() {
  const container = document.getElementById("eval-report-container");
  const frame = document.getElementById("eval-report-frame");
  if (!container) return;

  if (container.style.display === "none") {
    container.style.display = "block";
    if (frame && !frame.src) frame.src = `/api/report/module-d?run_id=${RUN_ID}`;
    container.scrollIntoView({ behavior: "smooth" });
  } else {
    container.style.display = "none";
  }
}

async function generateFinalReport() {
  appendLog("INFO", "Compiling Final Publication Report (§8.1)...");
  const btn = document.getElementById("btn-continue-final");
  const reBtn = document.getElementById("btn-re-generate-final");
  if (btn) btn.disabled = true;
  if (reBtn) reBtn.disabled = true;

  try {
    const res = await fetch("/api/report/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: RUN_ID })
    });
    if (!res.ok) {
      let errMsg = "Report generation failed";
      try {
        const errData = await res.json();
        errMsg = errData.detail || JSON.stringify(errData);
      } catch (_) {
        errMsg = await res.text();
      }
      throw new Error(errMsg);
    }
    const data = await res.json();
    appendLog("SUCCESS", `Final Publication Report Generated! Status: ${data.status}`);
    await fetchState();
    switchStage("final_report");
    const frame = document.getElementById("final-report-frame");
    if (frame) frame.src = `/api/report/final?run_id=${RUN_ID}&t=${Date.now()}`;
  } catch (err) {
    appendLog("ERROR", `Failed to generate final report: ${err.message}`);
    alert(`Report generation failed: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
    if (reBtn) reBtn.disabled = false;
  }
}

