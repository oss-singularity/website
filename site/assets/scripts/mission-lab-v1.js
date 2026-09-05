(() => {
  const form = document.querySelector("#mission-form");
  const presetInput = document.querySelector("#mission-preset");
  const goalInput = document.querySelector("#mission-goal");
  const topologyInput = document.querySelector("#mission-topology");
  const boundaryInput = document.querySelector("#mission-boundary");
  const output = document.querySelector("#mission-output");
  const status = document.querySelector("#mission-status");

  if (!form || !presetInput || !goalInput || !topologyInput || !boundaryInput || !output || !status) {
    return;
  }

  // Keep these public presets identical to site/data/missions.json. No fetch is needed.
  const presets = [
    {
      id: "ship-feature",
      title: "Ship a useful feature",
      summary: "Turn a concrete user need into a small, reviewable change with evidence.",
      goal: "Add one useful feature to an existing open-source project. Start with the user problem and preserve the behavior people already rely on.",
      deliverable: "A reviewable patch, a short explanation of the resulting behavior, and relevant validation evidence.",
      constraints: [
        "Read the project instructions and existing implementation before proposing changes.",
        "Keep the change focused on one user outcome and preserve unrelated work.",
        "Do not publish, deploy, send messages, or change external services without separate authorization.",
      ],
      acceptance: [
        "The original user problem and intended behavior are stated clearly.",
        "The change follows the project's conventions and includes relevant validation.",
        "The handoff lists changed files, evidence, limitations, and the next review action.",
      ],
    },
    {
      id: "research-map",
      title: "Map an unfamiliar topic",
      summary: "Build a source-backed research map with useful distinctions and honest unknowns.",
      goal: "Investigate an unfamiliar agent tool, protocol, or technical idea. Explain what it does, where it fits, and what remains uncertain.",
      deliverable: "A concise research map with primary-source links, compared approaches, dated observations, and open questions.",
      constraints: [
        "Prefer primary sources and distinguish documented behavior from inference.",
        "Record source dates and do not treat popularity as evidence of suitability.",
        "Do not create accounts, purchase services, or submit private material to external tools.",
      ],
      acceptance: [
        "Every material factual claim has a supporting source or an explicit uncertainty label.",
        "The map explains practical differences, tradeoffs, and a concrete starting point.",
        "The handoff includes unanswered questions and the evidence needed to resolve them.",
      ],
    },
    {
      id: "audit-project",
      title: "Audit a project",
      summary: "Find actionable reliability and contributor-experience problems before changing anything.",
      goal: "Review an open-source project for reliability, maintainability, and contributor experience. Prioritize reproducible findings over a long list of guesses.",
      deliverable: "A prioritized findings report with file references, reproduction steps, impact, and focused remediation proposals.",
      constraints: [
        "Inspect only the authorized project and do not probe unrelated systems.",
        "Do not run untrusted project code or destructive checks without an appropriate review and execution boundary.",
        "Keep private data and credentials out of logs, reports, and exports.",
      ],
      acceptance: [
        "Each finding identifies a concrete trigger, observed behavior, and practical impact.",
        "Evidence separates confirmed problems from hypotheses requiring further checks.",
        "The handoff states review coverage, remaining uncertainty, and suggested next actions.",
      ],
    },
  ];

  const topologies = {
    solo: { label: "One agent", roles: ["Generalist"] },
    pair: { label: "Builder + reviewer", roles: ["Builder", "Reviewer"] },
    crew: { label: "Four-role crew", roles: ["Scout", "Builder", "Reviewer", "Coordinator"] },
  };
  const boundaries = {
    "read-only": {
      label: "Read only",
      rule: "Inspect authorized material and return a proposal or report. Do not change project files, run project code, or modify external systems.",
    },
    workspace: {
      label: "Local workspace",
      rule: "Within a separately authorized workspace, prepare local changes and relevant checks. Publishing, deployment, messaging, spending, and external changes require separate authorization.",
    },
  };
  const deliverableInput = document.querySelector("#mission-deliverable");
  const constraintsInput = document.querySelector("#mission-constraints");
  const copyButton = document.querySelector("#mission-copy");
  const downloadButton = document.querySelector("#mission-download");
  const jsonButton = document.querySelector("#mission-json");
  const startButton = document.querySelector("#simulation-start");
  const resetButton = document.querySelector("#simulation-reset");
  const simulationSteps = document.querySelector("#simulation-steps");
  const simulationStatus = document.querySelector("#simulation-status");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const stageNames = ["observe", "plan", "build", "review", "handoff"];
  let timer = null;
  let running = false;
  let simulationShown = false;

  const currentPreset = () => presets.find((preset) => preset.id === presetInput.value) || presets[0];

  const createBrief = () => {
    const preset = currentPreset();
    const topology = Object.hasOwn(topologies, topologyInput.value) ? topologyInput.value : "solo";
    const boundary = Object.hasOwn(boundaries, boundaryInput.value) ? boundaryInput.value : "read-only";
    const constraints = constraintsInput
      ? constraintsInput.value.split("\n").map((line) => line.trim()).filter(Boolean)
      : preset.constraints;
    return {
      schema_version: "1.0",
      kind: "mission-brief",
      source: "https://oss-singularity.io/lab/",
      mission_id: preset.id,
      title: preset.title,
      goal: goalInput.value.trim() || preset.goal,
      deliverable: deliverableInput?.value.trim() || preset.deliverable,
      topology: { id: topology, ...topologies[topology] },
      boundary: { id: boundary, ...boundaries[boundary] },
      constraints,
      acceptance: preset.acceptance,
      handoff: ["Outcome or proposed outcome", "Evidence and source references", "Uncertainty and limitations", "Next action and required authorization"],
      execution: "This is a planning brief. No agent has run. The recipient must confirm scope and authorization before execution.",
    };
  };

  const markdown = (brief) => [
    `# Mission brief: ${brief.title}`,
    "",
    `Preset: ${brief.mission_id} · Format: ${brief.schema_version}`,
    "",
    "## Goal",
    brief.goal,
    "",
    "## Deliverable",
    brief.deliverable,
    "",
    "## Team",
    `${brief.topology.label}: ${brief.topology.roles.join(" → ")}`,
    "",
    "## Execution boundary",
    `${brief.boundary.label}. ${brief.boundary.rule}`,
    "The execution boundary takes precedence over the requested deliverable. For read-only work, return a proposal instead of modifying files.",
    "",
    "## Constraints",
    ...brief.constraints.map((constraint) => `- ${constraint}`),
    "",
    "## Acceptance criteria",
    ...brief.acceptance.map((criterion) => `- [ ] ${criterion}`),
    "",
    "## Handoff",
    ...brief.handoff.map((item) => `- ${item}`),
    "",
    brief.execution,
    "",
    `Created locally at ${brief.source}`,
  ].join("\n");

  const renderBrief = () => {
    output.textContent = markdown(createBrief());
  };

  const stageDetails = (brief) => {
    const solo = brief.topology.id === "solo";
    const crew = brief.topology.id === "crew";
    const builder = solo ? "Generalist" : "Builder";
    const reviewer = solo ? "Generalist, in a separate review pass," : "Reviewer";
    return [
      `${crew ? "Scout" : builder} would inspect the authorized context and identify unknowns.`,
      `${crew ? "Coordinator" : builder} would divide the goal into concrete steps and define evidence.`,
      `${builder} would ${brief.boundary.id === "read-only" ? "prepare a proposal or report without changing files" : "prepare the local deliverable inside the authorized workspace"}.`,
      `${reviewer} would compare the proposed result with the acceptance criteria.`,
      `${crew ? "Coordinator" : builder} would return evidence, limitations, and the next review action.`,
    ];
  };

  const setStage = (stage, state) => {
    const element = simulationSteps?.querySelector(`[data-stage="${stage}"]`);
    if (!element) {
      return;
    }
    element.dataset.state = state;
    element.classList.toggle("is-active", state === "active");
    element.classList.toggle("is-complete", state === "complete");
    if (state === "active") {
      element.setAttribute("aria-current", "step");
    } else {
      element.removeAttribute("aria-current");
    }
  };

  const resetSimulation = (message = "Simulation ready. No agents or tools will run.") => {
    window.clearTimeout(timer);
    timer = null;
    running = false;
    simulationShown = false;
    const details = stageDetails(createBrief());
    stageNames.forEach((stage, index) => {
      setStage(stage, "idle");
      const detail = simulationSteps?.querySelector(`[data-stage="${stage}"] [data-stage-detail]`);
      if (detail) {
        detail.textContent = details[index];
      }
    });
    simulationSteps?.setAttribute("aria-busy", "false");
    if (startButton) {
      startButton.disabled = false;
    }
    if (resetButton) {
      resetButton.disabled = true;
    }
    if (simulationStatus) {
      simulationStatus.textContent = message;
    }
  };

  const completeSimulation = () => {
    window.clearTimeout(timer);
    timer = null;
    running = false;
    stageNames.forEach((stage) => setStage(stage, "complete"));
    simulationSteps.setAttribute("aria-busy", "false");
    startButton.disabled = false;
    simulationStatus.textContent = "Simulation complete: five planned stages illustrated. No agent ran, no work was executed, and no data was sent.";
  };

  const startSimulation = () => {
    resetSimulation();
    running = true;
    simulationShown = true;
    startButton.disabled = true;
    if (resetButton) {
      resetButton.disabled = false;
    }
    simulationSteps.setAttribute("aria-busy", "true");
    if (reducedMotion.matches) {
      completeSimulation();
      return;
    }
    const details = stageDetails(createBrief());
    const advance = (index) => {
      if (!running) {
        return;
      }
      if (index > 0) {
        setStage(stageNames[index - 1], "complete");
      }
      if (index === stageNames.length) {
        completeSimulation();
        return;
      }
      setStage(stageNames[index], "active");
      simulationStatus.textContent = `Simulation · ${index + 1} of 5: ${details[index]}`;
      timer = window.setTimeout(() => advance(index + 1), 1400);
    };
    advance(0);
  };

  const loadPreset = () => {
    const preset = currentPreset();
    goalInput.value = preset.goal;
    if (deliverableInput) {
      deliverableInput.value = preset.deliverable;
    }
    if (constraintsInput) {
      constraintsInput.value = preset.constraints.join("\n");
    }
  };

  const download = (format) => {
    const brief = createBrief();
    const content = format === "json" ? `${JSON.stringify(brief, null, 2)}\n` : `${markdown(brief)}\n`;
    const type = format === "json" ? "application/json;charset=utf-8" : "text/markdown;charset=utf-8";
    const url = URL.createObjectURL(new Blob([content], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `oss-singularity-${brief.mission_id}.${format}`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `${format === "json" ? "JSON" : "Markdown"} download prepared locally. Your browser controls where it is saved.`;
  };

  const updateForm = (event) => {
    if (event.type === "change" && event.target === presetInput) {
      loadPreset();
    }
    renderBrief();
    resetSimulation(simulationShown ? "Brief changed. Simulation reset; no work was executed." : undefined);
    status.textContent = "Brief updated locally. Copy or download it when ready.";
  };

  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("input", updateForm);
  form.addEventListener("change", updateForm);
  copyButton?.addEventListener("click", async () => {
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard unavailable");
      }
      await navigator.clipboard.writeText(output.textContent);
      status.textContent = "Mission brief copied as Markdown.";
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(output);
      selection?.removeAllRanges();
      selection?.addRange(range);
      status.textContent = "Clipboard access is unavailable. The brief is selected; use your browser's Copy command or download it.";
    }
  });
  downloadButton?.addEventListener("click", () => download("md"));
  jsonButton?.addEventListener("click", () => download("json"));
  if (startButton && simulationSteps && simulationStatus) {
    startButton.addEventListener("click", startSimulation);
    resetButton?.addEventListener("click", () => resetSimulation());
  }
  reducedMotion.addEventListener("change", () => {
    if (running && reducedMotion.matches) {
      completeSimulation();
    }
  });
  window.addEventListener("pagehide", () => {
    if (running) {
      resetSimulation("Simulation reset after leaving the page. No work was executed.");
    }
  });

  const requestedMission = new URLSearchParams(window.location.search).get("mission");
  if (presets.some((preset) => preset.id === requestedMission)) {
    presetInput.value = requestedMission;
  }
  loadPreset();
  renderBrief();
  resetSimulation();
  [copyButton, downloadButton, jsonButton].forEach((button) => {
    if (button) {
      button.disabled = false;
    }
  });
  status.textContent = "Your brief stays in this page until you copy or download it. The brief is not sent or saved by the site.";
})();
