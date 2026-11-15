const SAFE_ERROR_KEYS = Object.freeze({
  auth_invalid_credentials: 'errors.authInvalid',
  auth_rate_limited: 'errors.authRateLimited',
  auth_registration_unavailable: 'errors.authRegistration',
  auth_request_invalid: 'errors.authValidation',
  auth_required: 'errors.authRequired',
  auth_csrf_invalid: 'errors.authCsrf',
  auth_storage_unavailable: 'errors.authUnavailable',
  auth_disabled: 'errors.authUnavailable'
});

async function safeBody(response) {
  try { return await response.json(); } catch { return {}; }
}

export function createAuthClient({fetchImpl = fetch, onChange = () => {}} = {}) {
  let snapshot = Object.freeze({ready:false, enabled:false, session:null, errorKey:null, busy:false});
  const publish = update => {
    snapshot = Object.freeze({...snapshot, ...update});
    onChange(snapshot);
    return snapshot;
  };
  const errorKey = body => SAFE_ERROR_KEYS[body?.detail?.code] || 'errors.authUnknown';
  async function bootstrap() {
    let configured = false;
    try {
      const config = await fetchImpl('/auth-api/config', {headers:{Accept:'application/json'}});
      if (!config.ok) return publish({ready:true,enabled:false,session:null,errorKey:null});
      const value = await safeBody(config);
      if (!value.enabled) return publish({ready:true,enabled:false,session:null,errorKey:null});
      configured = true;
      const current = await fetchImpl('/auth-api/session', {headers:{Accept:'application/json'}});
      if (current.status === 401) return publish({ready:true,enabled:true,session:null,errorKey:null});
      if (!current.ok) return publish({ready:true,enabled:true,session:null,errorKey:'errors.authUnavailable'});
      return publish({ready:true,enabled:true,session:await safeBody(current),errorKey:null});
    } catch {
      return publish({
        ready:true, enabled:configured, session:null,
        errorKey:configured ? 'errors.authUnavailable' : null
      });
    }
  }
  async function authenticate(mode, payload) {
    if (!snapshot.enabled || snapshot.busy) return false;
    publish({busy:true,errorKey:null});
    try {
      const response = await fetchImpl(`/auth-api/${mode}`, {
        method:'POST', headers:{'Content-Type':'application/json',Accept:'application/json'},
        body:JSON.stringify(payload)
      });
      const body = await safeBody(response);
      if (!response.ok) { publish({busy:false,errorKey:errorKey(body)}); return false; }
      publish({busy:false,session:body,errorKey:null}); return true;
    } catch {
      publish({busy:false,errorKey:'errors.authUnavailable'}); return false;
    }
  }
  async function logout() {
    if (!snapshot.session || snapshot.busy) return false;
    publish({busy:true,errorKey:null});
    try {
      const response = await fetchImpl('/auth-api/logout', {
        method:'POST', headers:{'X-CSRF-Token':snapshot.session.csrf_token}
      });
      if (!response.ok) { publish({busy:false,errorKey:errorKey(await safeBody(response))}); return false; }
      publish({busy:false,session:null,errorKey:null}); return true;
    } catch {
      publish({busy:false,errorKey:'errors.authUnavailable'}); return false;
    }
  }
  return {
    bootstrap, authenticate, logout,
    get snapshot() { return snapshot; },
    get enabled() { return snapshot.enabled; },
    get session() { return snapshot.session; }
  };
}
