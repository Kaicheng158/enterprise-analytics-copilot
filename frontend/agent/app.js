const form = document.querySelector("#task-form");
const statusNode = document.querySelector("#status");
const resultNode = document.querySelector("#result");

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function safeMessage(body, fallback) {
  return body?.detail?.message || body?.error?.message || fallback;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(safeMessage(body, `Request failed with HTTP ${response.status}`));
    error.status = response.status;
    error.code = body?.detail?.code;
    throw error;
  }
  return body;
}

function showTask(task) {
  statusNode.textContent = `${task.status} · ${task.progress.completed_steps}/${task.progress.total_steps} steps · ${task.task_id}`;
  if (task.status === "completed") {
    const value = task.result;
    resultNode.textContent = [
      `${value.baseline_period}: ${value.currency} ${value.baseline_total_sales}`,
      `${value.comparison_period}: ${value.currency} ${value.comparison_total_sales}`,
      `Absolute change: ${value.currency} ${value.absolute_change}`,
      `Growth rate: ${value.growth_rate_percent}%`,
      `Region: ${value.region}`,
      `Source: ${value.source_relation}`,
      `Dataset: ${value.dataset_version}`,
    ].join("\n");
  } else if (task.error) {
    resultNode.textContent = task.error.message;
  }
}

async function pollTask(taskId, accessKey) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    let task;
    try {
      task = await requestJson(`/internal/agent/tasks/${taskId}`, {
        headers: {"X-Internal-Agent-Key": accessKey},
        cache: "no-store",
      });
    } catch (error) {
      if (error.status === 409 && error.code === "agent_status_changed") {
        await sleep(100);
        continue;
      }
      throw error;
    }
    showTask(task);
    if (["completed", "failed", "needs_reconciliation"].includes(task.status)) return;
    await sleep(500);
  }
  throw new Error("Task is still running; use its task ID to query status again.");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const accessKey = document.querySelector("#access-key").value;
  const baseline = document.querySelector("#baseline-period").value;
  const comparison = document.querySelector("#comparison-period").value;
  if (baseline === comparison) {
    statusNode.textContent = "Choose two different periods.";
    return;
  }
  statusNode.textContent = "Submitting…";
  resultNode.textContent = "";
  try {
    const task = await requestJson("/internal/agent/tasks", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Agent-Key": accessKey,
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        task: "compare_quarterly_sales",
        baseline_period: baseline,
        comparison_period: comparison,
        region: document.querySelector("#region").value,
      }),
    });
    showTask(task);
    await pollTask(task.task_id, accessKey);
  } catch (error) {
    statusNode.textContent = "Request stopped safely.";
    resultNode.textContent = error instanceof Error ? error.message : "Unknown error";
  }
});
