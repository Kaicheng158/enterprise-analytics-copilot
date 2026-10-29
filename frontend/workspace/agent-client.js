const STORAGE_KEY = 'eac.workspace.agent-recovery.v1';
const STATUSES = new Set(['queued', 'running', 'completed', 'failed', 'needs_reconciliation']);
export const TERMINAL_STATUSES = new Set(['completed', 'failed', 'needs_reconciliation']);

export class AgentClientError extends Error {
  constructor(key, status = 0, code = '') { super(key); this.key = key; this.status = status; this.code = code; }
}

function errorKey(status) {
  return ({401:'errors.unauthorized',403:'errors.forbidden',404:'errors.notFound',409:'errors.conflict',422:'errors.validation',429:'errors.capacity',502:'errors.planner',503:'errors.unavailable'})[status] || 'errors.unknown';
}

function validRequest(value) {
  return value && value.task === 'compare_quarterly_sales'
    && ['2026-Q1','2026-Q2','2026-Q3'].includes(value.baseline_period)
    && ['2026-Q1','2026-Q2','2026-Q3'].includes(value.comparison_period)
    && value.baseline_period !== value.comparison_period
    && ['all','north','south'].includes(value.region)
    && Object.keys(value).length === 4;
}

function validateTask(value) {
  if (!value || value.response_version !== 'internal-agent-task-v1' || !STATUSES.has(value.status)
    || typeof value.task_id !== 'string' || !validRequest(value.request)
    || !value.progress || !Number.isInteger(value.progress.completed_steps)
    || !Number.isInteger(value.progress.total_steps) || !Number.isInteger(value.state_version)) {
    throw new AgentClientError('errors.unknown');
  }
  if (value.status === 'completed' && (!value.result || value.error)) throw new AgentClientError('errors.unknown');
  if (TERMINAL_STATUSES.has(value.status) && value.status !== 'completed' && !value.error) throw new AgentClientError('errors.unknown');
  return value;
}

function sameRequest(left, right) { return JSON.stringify(left) === JSON.stringify(right); }

export function createAgentClient({
  fetchImpl = window.fetch.bind(window), storage = window.sessionStorage,
  onChange = () => {}, setTimer = window.setTimeout.bind(window), clearTimer = window.clearTimeout.bind(window),
  uuid = () => crypto.randomUUID(), pollIntervalMs = 600, maxPolls = 30
} = {}) {
  let timer = null; let submitPromise = null; let pollSequence = 0; let pollCount = 0;
  let snapshot = {phase:'idle', task:null, recovery:null, errorKey:null, polling:false};
  const emit = () => onChange(structuredClone(snapshot));
  const save = () => {
    try {
      if (snapshot.recovery) storage.setItem(STORAGE_KEY, JSON.stringify(snapshot.recovery));
      else storage.removeItem(STORAGE_KEY);
    } catch { /* Session storage is optional; in-memory safety still applies. */ }
  };
  const stopTimer = () => { if (timer !== null) clearTimer(timer); timer = null; };
  const schedule = () => {
    stopTimer();
    if (!snapshot.task || TERMINAL_STATUSES.has(snapshot.task.status)) return;
    if (pollCount >= maxPolls) { snapshot = {...snapshot, errorKey:'errors.pollLimit', polling:false}; emit(); return; }
    timer = setTimer(() => { void poll(); }, pollIntervalMs);
  };
  const newer = task => {
    if (!snapshot.task) return true;
    if (task.task_id !== snapshot.task.task_id) return false;
    if (task.state_version !== snapshot.task.state_version) return task.state_version > snapshot.task.state_version;
    return Date.parse(task.updated_at || 0) >= Date.parse(snapshot.task.updated_at || 0);
  };
  const applyTask = task => {
    if (!newer(task)) return false;
    snapshot = {...snapshot, phase:task.status, task, errorKey:null, polling:false,
      recovery:{...snapshot.recovery, taskId:task.task_id}};
    save(); emit();
    if (TERMINAL_STATUSES.has(task.status)) stopTimer(); else schedule();
    return true;
  };
  const parse = async response => {
    let body;
    try { body = await response.json(); } catch { throw new AgentClientError('errors.unknown', response.status); }
    if (!response.ok) throw new AgentClientError(errorKey(response.status), response.status, body?.detail?.code || '');
    return validateTask(body);
  };
  const post = async recovery => {
    snapshot = {...snapshot, phase:'submitting', recovery, errorKey:null}; save(); emit();
    try {
      const response = await fetchImpl('/workspace-api/tasks', {
        method:'POST', headers:{'Content-Type':'application/json','Idempotency-Key':recovery.key},
        body:JSON.stringify(recovery.request)
      });
      return applyTask(await parse(response));
    } catch (error) {
      const key = error instanceof AgentClientError ? error.key : 'errors.network';
      snapshot = {...snapshot, phase:'submission_unknown', errorKey:key, polling:false}; save(); emit();
      return false;
    }
  };
  const submit = request => {
    if (!validRequest(request)) throw new AgentClientError('errors.validation', 422);
    if (submitPromise) return submitPromise;
    if (snapshot.task && !TERMINAL_STATUSES.has(snapshot.task.status)) return Promise.resolve(false);
    const recovery = snapshot.phase === 'submission_unknown' && snapshot.recovery && sameRequest(snapshot.recovery.request, request)
      ? snapshot.recovery : {key:`workspace-${uuid()}`, request, taskId:null};
    if (recovery !== snapshot.recovery) pollCount = 0;
    submitPromise = post(recovery).finally(() => { submitPromise = null; });
    return submitPromise;
  };
  const recover = () => {
    if (!snapshot.recovery) return Promise.resolve(false);
    if (snapshot.recovery.taskId) return poll();
    if (submitPromise) return submitPromise;
    submitPromise = post(snapshot.recovery).finally(() => { submitPromise = null; });
    return submitPromise;
  };
  async function poll() {
    if (!snapshot.recovery?.taskId || TERMINAL_STATUSES.has(snapshot.task?.status)) return false;
    const sequence = ++pollSequence; pollCount += 1;
    snapshot = {...snapshot, polling:true, errorKey:null}; emit();
    try {
      const response = await fetchImpl(`/workspace-api/tasks/${snapshot.recovery.taskId}`, {method:'GET'});
      const task = await parse(response);
      if (sequence < pollSequence && !newer(task)) return false;
      return applyTask(task);
    } catch (error) {
      if (error instanceof AgentClientError && error.code === 'agent_status_changed') {
        snapshot = {...snapshot, polling:false, errorKey:null}; emit(); schedule(); return false;
      }
      const key = error instanceof AgentClientError ? error.key : 'errors.network';
      snapshot = {...snapshot, polling:false, errorKey:key}; emit(); stopTimer();
      return false;
    }
  }
  const hydrate = () => {
    try {
      const raw = storage.getItem(STORAGE_KEY); if (!raw) return emit();
      const recovery = JSON.parse(raw);
      if (!recovery || typeof recovery.key !== 'string' || !validRequest(recovery.request)
        || (recovery.taskId !== null && typeof recovery.taskId !== 'string')) throw new Error('invalid');
      snapshot = {...snapshot, phase:recovery.taskId ? 'recovering' : 'submission_unknown', recovery}; emit();
      if (recovery.taskId) void poll();
    } catch { try { storage.removeItem(STORAGE_KEY); } catch {} emit(); }
  };
  const clear = () => {
    if (snapshot.task && !TERMINAL_STATUSES.has(snapshot.task.status)) return false;
    stopTimer(); pollCount = 0; snapshot = {phase:'idle', task:null, recovery:null, errorKey:null, polling:false}; save(); emit(); return true;
  };
  return {submit, recover, poll, hydrate, clear, getSnapshot:() => structuredClone(snapshot)};
}

export const agentRecoveryStorageKey = STORAGE_KEY;
