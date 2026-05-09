/* ModelWatch frontend — vanilla JS, no build step.
 *
 * One file powers both the marketing site (signup form + checkout buttons)
 * and the dashboard (auth, CRUD on endpoints/specs/keys, drift events).
 *
 * The API base URL is detected from window.location: when served from
 * modelwatch.app, we hit api.modelwatch.app. Override via window.MW_API_BASE
 * for local dev (e.g. Cloudflare Pages preview URLs). */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // API base URL detection
  // ---------------------------------------------------------------------------
  function detectApiBase() {
    if (window.MW_API_BASE) return window.MW_API_BASE;
    var h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8000';
    // Production: serve from modelwatch.app, hit api.modelwatch.app.
    // Cloudflare Pages preview: foo.modelwatch-web.pages.dev → use prod API.
    return 'https://api.modelwatch.app';
  }
  var API = detectApiBase();

  // ---------------------------------------------------------------------------
  // Auth: API key in sessionStorage (clears on close), with localStorage opt-in
  // ---------------------------------------------------------------------------
  var KEY_NAME = 'mw_api_key';
  function getKey() { return sessionStorage.getItem(KEY_NAME) || localStorage.getItem(KEY_NAME); }
  function setKey(k, persist) {
    sessionStorage.setItem(KEY_NAME, k);
    if (persist) localStorage.setItem(KEY_NAME, k);
  }
  function clearKey() {
    sessionStorage.removeItem(KEY_NAME);
    localStorage.removeItem(KEY_NAME);
  }

  // ---------------------------------------------------------------------------
  // HTTP helper
  // ---------------------------------------------------------------------------
  function api(method, path, body) {
    var headers = { 'Content-Type': 'application/json' };
    var k = getKey();
    if (k) headers['Authorization'] = 'Bearer ' + k;
    return fetch(API + path, {
      method: method,
      headers: headers,
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      var ctype = r.headers.get('content-type') || '';
      if (ctype.indexOf('application/json') === -1) {
        return r.text().then(function (t) {
          if (!r.ok) throw { status: r.status, message: t || r.statusText };
          return t;
        });
      }
      return r.json().then(function (j) {
        if (!r.ok) throw { status: r.status, message: (j && (j.detail || j.message)) || r.statusText, data: j };
        return j;
      });
    });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function severityColor(sev) {
    return ({
      none: 'bg-slate-100 text-slate-600',
      low: 'bg-emerald-100 text-emerald-800',
      medium: 'bg-amber-100 text-amber-800',
      high: 'bg-rose-100 text-rose-800',
      critical: 'bg-rose-600 text-white',
    })[sev] || 'bg-slate-100 text-slate-600';
  }

  // ---------------------------------------------------------------------------
  // SIGNUP form (landing page)
  // ---------------------------------------------------------------------------
  var signupForm = document.getElementById('signup-form');
  if (signupForm) {
    signupForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var fd = new FormData(signupForm);
      var result = document.getElementById('signup-result');
      result.textContent = 'Creating workspace...';
      result.className = 'mt-4 text-center text-sm text-slate-600';
      api('POST', '/api/auth/signup', {
        email: fd.get('email'),
        workspace_name: fd.get('workspace_name'),
      }).then(function (data) {
        if (data.api_key) {
          // SendGrid disabled — show key inline (dev mode)
          result.innerHTML = '<span class="text-emerald-700">Created!</span> Your key: <code class="bg-slate-100 px-2 py-1 rounded mono">' + escapeHtml(data.api_key) + '</code>';
        } else {
          result.innerHTML = '<span class="text-emerald-700">Created!</span> Check <strong>' + escapeHtml(fd.get('email')) + '</strong> for your API key. <a href="/dashboard.html" class="text-blue-600 hover:underline">Go to dashboard &rarr;</a>';
        }
        signupForm.reset();
      }).catch(function (err) {
        result.innerHTML = '<span class="text-rose-700">Failed:</span> ' + escapeHtml(err.message || 'Signup error');
      });
    });
  }

  // ---------------------------------------------------------------------------
  // CHECKOUT buttons (landing + dashboard)
  // ---------------------------------------------------------------------------
  document.querySelectorAll('[data-checkout]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tier = btn.getAttribute('data-checkout');
      if (!getKey()) {
        // Not signed in — bounce to dashboard which prompts for key
        sessionStorage.setItem('mw_pending_checkout', tier);
        window.location.href = '/dashboard.html';
        return;
      }
      btn.disabled = true;
      btn.textContent = 'Opening Stripe...';
      api('POST', '/api/billing/checkout', { tier: tier }).then(function (data) {
        window.location.href = data.checkout_url;
      }).catch(function (err) {
        btn.disabled = false;
        alert('Checkout failed: ' + (err.message || 'unknown error'));
      });
    });
  });

  // ---------------------------------------------------------------------------
  // DASHBOARD
  // ---------------------------------------------------------------------------
  var loginView = document.getElementById('login-view');
  var appView = document.getElementById('app-view');
  if (!loginView || !appView) return; // not on dashboard page

  function showApp() {
    loginView.hidden = true;
    appView.hidden = false;
    document.getElementById('logout-btn').hidden = false;
    refreshAll();
    // honor pending checkout from landing
    var pending = sessionStorage.getItem('mw_pending_checkout');
    if (pending) {
      sessionStorage.removeItem('mw_pending_checkout');
      api('POST', '/api/billing/checkout', { tier: pending }).then(function (d) {
        window.location.href = d.checkout_url;
      });
    }
  }
  function showLogin() {
    loginView.hidden = false;
    appView.hidden = true;
    document.getElementById('logout-btn').hidden = true;
  }

  // Login form
  document.getElementById('login-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var key = document.getElementById('api-key-input').value.trim();
    var err = document.getElementById('login-error');
    err.textContent = '';
    if (!/^mw_/.test(key)) { err.textContent = 'Keys start with mw_'; return; }
    setKey(key, true);
    api('GET', '/api/workspaces/me').then(showApp).catch(function (e2) {
      clearKey();
      err.textContent = 'Invalid key (' + (e2.status || 'network') + ')';
    });
  });

  document.getElementById('logout-btn').addEventListener('click', function () {
    clearKey();
    showLogin();
  });

  // Modal helpers
  function bindModal(id) {
    var dlg = document.getElementById(id);
    dlg.querySelectorAll('[data-close]').forEach(function (b) {
      b.addEventListener('click', function () { dlg.close(); });
    });
    return dlg;
  }
  var endpointModal = bindModal('endpoint-modal');
  var specModal = bindModal('spec-modal');
  var llmKeyModal = bindModal('llm-key-modal');

  document.getElementById('add-endpoint-btn').addEventListener('click', function () { endpointModal.showModal(); });
  document.getElementById('add-llm-key-btn').addEventListener('click', function () { llmKeyModal.showModal(); });
  document.getElementById('add-spec-btn').addEventListener('click', function () {
    populateSpecEndpoints().then(function () { specModal.showModal(); });
  });

  // Endpoint form submit
  document.getElementById('endpoint-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var fd = new FormData(e.target);
    var body = {
      name: fd.get('name'),
      provider: fd.get('provider'),
      model: fd.get('model'),
    };
    if (fd.get('base_url')) body.base_url = fd.get('base_url');
    api('POST', '/api/endpoints', body).then(function () {
      e.target.reset();
      endpointModal.close();
      refreshEndpoints();
    }).catch(function (err) { alert('Failed: ' + err.message); });
  });

  // LLM key form submit
  document.getElementById('llm-key-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var fd = new FormData(e.target);
    api('POST', '/api/workspaces/me/api-keys', {
      provider: fd.get('provider'),
      api_key: fd.get('api_key'),
    }).then(function () {
      e.target.reset();
      llmKeyModal.close();
      refreshLlmKeys();
    }).catch(function (err) { alert('Failed: ' + err.message); });
  });

  // Spec form submit
  document.getElementById('spec-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var fd = new FormData(e.target);
    api('POST', '/api/specs', {
      endpoint_id: fd.get('endpoint_id'),
      name: fd.get('name'),
      prompt: fd.get('prompt'),
      frequency: fd.get('frequency'),
      threshold: fd.get('threshold'),
    }).then(function () {
      e.target.reset();
      specModal.close();
      refreshSpecs();
    }).catch(function (err) { alert('Failed: ' + err.message); });
  });

  function populateSpecEndpoints() {
    return api('GET', '/api/endpoints').then(function (data) {
      var sel = document.querySelector('#spec-form select[name=endpoint_id]');
      var rows = data.items || data;
      sel.innerHTML = rows.map(function (e) {
        return '<option value="' + escapeHtml(e.id) + '">' + escapeHtml(e.name) + ' &middot; ' + escapeHtml(e.provider) + '/' + escapeHtml(e.model) + '</option>';
      }).join('') || '<option value="" disabled>Add an endpoint first</option>';
    });
  }

  // ---------------------------------------------------------------------------
  // Refresh helpers
  // ---------------------------------------------------------------------------
  function refreshAll() {
    refreshWorkspace();
    refreshLlmKeys();
    refreshEndpoints();
    refreshSpecs();
    refreshDrift();
    refreshHealth();
  }

  function refreshWorkspace() {
    api('GET', '/api/workspaces/me').then(function (ws) {
      var label = (ws.name || ws.workspace_name || 'Workspace') + ' &middot; ' + (ws.email || '');
      document.getElementById('ws-info').innerHTML = label;
      document.getElementById('kpi-plan').textContent = (ws.plan || 'free').toUpperCase();
    }).catch(function (err) {
      if (err.status === 401 || err.status === 403) { clearKey(); showLogin(); }
    });
  }

  function refreshHealth() {
    api('GET', '/api/dashboard/health').then(function (h) {
      document.getElementById('kpi-specs').textContent = (h.specs || 0) + ' / ' + (h.spec_limit || '∞');
      document.getElementById('kpi-runs').textContent = (h.runs_this_month || 0).toLocaleString() + ' / ' + ((h.runs_limit && h.runs_limit.toLocaleString()) || '∞');
      document.getElementById('kpi-drift').textContent = h.active_drift_events || 0;
    }).catch(function () { /* graceful: missing data */ });
  }

  function refreshLlmKeys() {
    api('GET', '/api/workspaces/me/api-keys').then(function (data) {
      var rows = data.items || data;
      var c = document.getElementById('llm-keys-list');
      if (!rows.length) { c.innerHTML = '<p class="text-sm text-slate-500 py-4">No keys yet — add one to start running specs.</p>'; return; }
      c.innerHTML = rows.map(function (k) {
        return '<div class="py-3 flex items-center justify-between">' +
          '<div><span class="font-medium">' + escapeHtml(k.provider) + '</span> ' +
          '<span class="mono text-slate-500 ml-2">****' + escapeHtml(k.last_4 || '') + '</span></div>' +
          '<button class="text-sm text-rose-600 hover:underline" data-del-key="' + escapeHtml(k.id) + '">Delete</button>' +
          '</div>';
      }).join('');
      c.querySelectorAll('[data-del-key]').forEach(function (b) {
        b.addEventListener('click', function () {
          if (!confirm('Delete this key? Specs using it will fail until replaced.')) return;
          api('DELETE', '/api/workspaces/me/api-keys/' + b.getAttribute('data-del-key')).then(refreshLlmKeys);
        });
      });
    }).catch(function () {
      document.getElementById('llm-keys-list').innerHTML = '<p class="text-sm text-slate-500 py-4">No keys yet.</p>';
    });
  }

  function refreshEndpoints() {
    api('GET', '/api/endpoints').then(function (data) {
      var rows = data.items || data;
      var c = document.getElementById('endpoints-list');
      if (!rows.length) { c.innerHTML = '<p class="text-sm text-slate-500 py-4">No endpoints yet.</p>'; return; }
      c.innerHTML = rows.map(function (e) {
        return '<div class="py-3 flex items-center justify-between">' +
          '<div><span class="font-medium">' + escapeHtml(e.name) + '</span> ' +
          '<span class="text-slate-500 mono ml-2">' + escapeHtml(e.provider) + '/' + escapeHtml(e.model) + '</span></div>' +
          '<button class="text-sm text-rose-600 hover:underline" data-del-ep="' + escapeHtml(e.id) + '">Delete</button>' +
          '</div>';
      }).join('');
      c.querySelectorAll('[data-del-ep]').forEach(function (b) {
        b.addEventListener('click', function () {
          if (!confirm('Delete this endpoint? Its specs will be deleted too.')) return;
          api('DELETE', '/api/endpoints/' + b.getAttribute('data-del-ep')).then(function () {
            refreshEndpoints(); refreshSpecs();
          });
        });
      });
    });
  }

  function refreshSpecs() {
    api('GET', '/api/specs').then(function (data) {
      var rows = data.items || data;
      var c = document.getElementById('specs-list');
      if (!rows.length) { c.innerHTML = '<p class="text-sm text-slate-500 py-4">No specs yet — add one to start drift monitoring.</p>'; return; }
      c.innerHTML = rows.map(function (s) {
        var lastSev = s.last_severity || 'none';
        return '<div class="border border-slate-200 rounded-lg p-4 flex items-start justify-between">' +
          '<div class="min-w-0 flex-1">' +
            '<div class="flex items-center gap-2"><span class="font-medium">' + escapeHtml(s.name) + '</span>' +
            '<span class="text-xs px-2 py-0.5 rounded-full ' + severityColor(lastSev) + '">' + escapeHtml(lastSev) + '</span>' +
            '<span class="text-xs text-slate-500 ml-2">every ' + escapeHtml(s.frequency) + '</span></div>' +
            '<p class="mt-1 text-sm text-slate-600 mono truncate">' + escapeHtml((s.prompt || '').slice(0, 120)) + '</p>' +
          '</div>' +
          '<div class="flex items-center gap-2 ml-4">' +
            '<button class="text-sm bg-slate-100 hover:bg-slate-200 rounded-md px-3 py-1.5" data-run-spec="' + escapeHtml(s.id) + '">Run now</button>' +
            '<button class="text-sm text-rose-600 hover:underline" data-del-spec="' + escapeHtml(s.id) + '">Delete</button>' +
          '</div>' +
        '</div>';
      }).join('');
      c.querySelectorAll('[data-run-spec]').forEach(function (b) {
        b.addEventListener('click', function () {
          var id = b.getAttribute('data-run-spec');
          b.disabled = true; b.textContent = 'Running...';
          api('POST', '/api/specs/' + id + '/run').then(function (r) {
            b.textContent = 'Drift ' + (r.drift_score || 0).toFixed(2);
            setTimeout(function () { b.textContent = 'Run now'; b.disabled = false; refreshDrift(); refreshSpecs(); }, 1500);
          }).catch(function (err) {
            b.textContent = 'Run now'; b.disabled = false;
            alert('Run failed: ' + err.message);
          });
        });
      });
      c.querySelectorAll('[data-del-spec]').forEach(function (b) {
        b.addEventListener('click', function () {
          if (!confirm('Delete this spec?')) return;
          api('DELETE', '/api/specs/' + b.getAttribute('data-del-spec')).then(refreshSpecs);
        });
      });
    });
  }

  function refreshDrift() {
    api('GET', '/api/dashboard/recent-events?limit=10').then(function (data) {
      var rows = data.items || data;
      var c = document.getElementById('drift-list');
      if (!rows.length) { c.innerHTML = '<p class="text-sm text-slate-500 py-4">No drift events yet — when one is detected, it shows here.</p>'; return; }
      c.innerHTML = rows.map(function (e) {
        var when = new Date(e.detected_at || e.created_at || Date.now()).toLocaleString();
        return '<div class="py-3 flex items-center justify-between">' +
          '<div class="min-w-0">' +
            '<span class="text-xs px-2 py-0.5 rounded-full ' + severityColor(e.severity) + '">' + escapeHtml(e.severity) + '</span> ' +
            '<span class="font-medium ml-1">' + escapeHtml(e.spec_name || e.spec_id) + '</span>' +
            '<span class="text-slate-500 text-sm ml-2">' + escapeHtml(when) + '</span>' +
            '<p class="text-xs text-slate-500 mt-1">drift score ' + (e.drift_score || 0).toFixed(2) + '</p>' +
          '</div>' +
          '<a class="text-sm text-blue-600 hover:underline" href="#" data-spec-link="' + escapeHtml(e.spec_id) + '">View spec</a>' +
        '</div>';
      }).join('');
    }).catch(function () {
      document.getElementById('drift-list').innerHTML = '<p class="text-sm text-slate-500 py-4">No drift events yet.</p>';
    });
  }

  // Boot
  if (getKey()) {
    api('GET', '/api/workspaces/me').then(showApp).catch(function () { clearKey(); showLogin(); });
  } else {
    showLogin();
  }
})();
