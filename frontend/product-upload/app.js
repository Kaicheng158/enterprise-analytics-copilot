(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = {
    csrf: null,
    projectId: sessionStorage.getItem("pv2_project_id"),
    revisionId: sessionStorage.getItem("pv2_dataset_revision_id"),
  };
  const show = (target, value) => { target.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2); };
  const encodeMetadata = (value) => {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  };
  const request = async (path, options = {}) => {
    const response = await fetch(path, { credentials:"same-origin", cache:"no-store", ...options });
    const payload = await response.json().catch(() => ({ detail:{ code:"invalid_response", message:"Invalid local response" } }));
    if (!response.ok) {
      const error = new Error(`${payload.detail?.code || "request_failed"}: ${payload.detail?.message || "Request failed"}`);
      error.payload = payload;
      throw error;
    }
    return payload;
  };
  const initialize = async () => {
    try {
      const session = await request("/auth-api/session");
      state.csrf = session.csrf_token;
      if (state.projectId) show($("project-result"), { project_id: state.projectId, resumed_from_session: true });
    } catch (_) {
      show($("project-result"), "Sign in through the local Workspace first, then return to /dataset-upload/.");
    }
  };
  $("create-project").addEventListener("click", async () => {
    const button = $("create-project"); button.disabled = true;
    const keyName = "pv2_project_idempotency_key";
    const key = sessionStorage.getItem(keyName) || crypto.randomUUID();
    sessionStorage.setItem(keyName, key);
    try {
      const result = await request("/product-api/v2/projects", {
        method:"POST",
        headers:{ "Content-Type":"application/json", "X-CSRF-Token":state.csrf, "Idempotency-Key":key },
        body:JSON.stringify({ contract_version:"create-private-project-v2", name:$("project-name").value }),
      });
      state.projectId = result.project_id;
      sessionStorage.setItem("pv2_project_id", state.projectId);
      show($("project-result"), result);
    } catch (error) { show($("project-result"), error.payload || error.message); }
    finally { button.disabled = false; }
  });
  $("upload-dataset").addEventListener("click", async () => {
    const button = $("upload-dataset"); button.disabled = true;
    try {
      if (!state.projectId) throw new Error("Create or resume a Project first.");
      const file = $("dataset-file").files[0];
      if (!file) throw new Error("Choose one synthetic CSV or XLSX file.");
      const columns = JSON.parse($("dataset-schema").value);
      const bytes = await file.arrayBuffer();
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) => byte.toString(16).padStart(2,"0")).join("");
      const keyName = `pv2_upload_key_${state.projectId}_${digest}`;
      const key = sessionStorage.getItem(keyName) || crypto.randomUUID();
      sessionStorage.setItem(keyName, key);
      const metadata = encodeMetadata({ contract_version:"dataset-upload-v1", dataset_name:$("dataset-name").value, filename:file.name, columns });
      const result = await request(`/product-api/v2/projects/${state.projectId}/datasets`, {
        method:"POST",
        headers:{ "Content-Type":file.type || "application/octet-stream", "X-CSRF-Token":state.csrf, "Idempotency-Key":key, "X-Dataset-Metadata":metadata },
        body:file,
      });
      state.revisionId = result.dataset_revision_id;
      sessionStorage.setItem("pv2_dataset_revision_id", state.revisionId);
      show($("upload-result"), result);
    } catch (error) { show($("upload-result"), error.payload || error.message); }
    finally { button.disabled = false; }
  });
  $("load-preview").addEventListener("click", async () => {
    try {
      if (!state.projectId || !state.revisionId) throw new Error("Store a validated Dataset first.");
      show($("preview-result"), await request(`/product-api/v2/projects/${state.projectId}/datasets/${state.revisionId}/preview`));
    } catch (error) { show($("preview-result"), error.payload || error.message); }
  });
  initialize();
})();
