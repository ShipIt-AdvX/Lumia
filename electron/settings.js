'use strict';
(function () {
  const DRAFT_KEY = 'lumia.settings.draft';
  const COUNTDOWN_SECONDS = 15;

  let baseline = {};
  let working = {};
  let dirty = false;
  let countdownTimer = null;

  function getPath(obj, path) {
    return path.split('.').reduce((n, k) => (n == null ? undefined : n[k]), obj);
  }
  function setPath(obj, path, value) {
    const keys = path.split('.');
    let node = obj;
    for (let i = 0; i < keys.length - 1; i++) {
      if (typeof node[keys[i]] !== 'object' || node[keys[i]] === null) node[keys[i]] = {};
      node = node[keys[i]];
    }
    node[keys[keys.length - 1]] = value;
  }
  function clone(o) { return JSON.parse(JSON.stringify(o == null ? {} : o)); }

  function saveDraft() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(working)); } catch (_) {}
  }
  function clearDraft() {
    try { localStorage.removeItem(DRAFT_KEY); } catch (_) {}
  }
  function loadDraft() {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  }

  const statusEl = document.getElementById('status');
  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = 'footer-status' + (cls ? ' ' + cls : '');
  }
  function markDirty() {
    dirty = true;
    saveDraft();
    setStatus('有未保存的更改', 'dirty');
  }

  function coerce(input, raw) {
    if (input.type === 'number') {
      const n = parseFloat(raw);
      return isNaN(n) ? 0 : n;
    }
    return raw;
  }

  function renderFields() {
    document.querySelectorAll('[data-path]').forEach((input) => {
      const val = getPath(working, input.dataset.path);
      if (val === undefined || val === null) return;
      input.value = val;
    });
    updateChairEnabled();
  }

  function bindFields() {
    document.querySelectorAll('[data-path]').forEach((input) => {
      const evt = (input.tagName === 'SELECT' || input.type === 'time') ? 'change' : 'input';
      input.addEventListener(evt, () => {
        setPath(working, input.dataset.path, coerce(input, input.value));
        if (input.dataset.path === 'chair.mode') updateChairEnabled();
        markDirty();
      });
    });
  }

  function renderList(container) {
    const path = container.dataset.list;
    const items = getPath(working, path) || [];
    container.innerHTML = '';
    items.forEach((item, idx) => {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = item;
      const x = document.createElement('span');
      x.className = 'x';
      x.textContent = '✕';
      x.title = '移除';
      x.addEventListener('click', () => {
        const arr = (getPath(working, path) || []).slice();
        arr.splice(idx, 1);
        setPath(working, path, arr);
        renderList(container);
        markDirty();
      });
      tag.appendChild(x);
      container.appendChild(tag);
    });
  }

  function bindListEditors() {
    document.querySelectorAll('[data-list]').forEach((container) => {
      const path = container.dataset.list;
      const field = container.closest('.field');
      const addRow = field ? field.querySelector('.tag-add') : null;
      if (!addRow) return;
      const input = addRow.querySelector('input');
      const addBtn = addRow.querySelector('[data-add]');

      const add = () => {
        const value = (input.value || '').trim();
        if (!value) return;
        const arr = (getPath(working, path) || []).slice();
        if (!arr.includes(value)) arr.push(value);
        setPath(working, path, arr);
        input.value = '';
        renderList(container);
        markDirty();
      };
      if (addBtn) addBtn.addEventListener('click', add);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); add(); }
      });
    });
  }

  function renderLists() {
    document.querySelectorAll('[data-list]').forEach(renderList);
  }

  function updateChairEnabled() {
    const mode = getPath(working, 'chair.mode');
    const relay = document.getElementById('relayUrl');
    if (relay) relay.disabled = (mode !== 'relay');
  }

  function bindChairTest() {
    const btn = document.getElementById('chairTest');
    const result = document.getElementById('chairTestResult');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      result.textContent = '测试中…';
      try {
        const r = await window.lumia.postJSON('/api/chair/stretch', { source: 'settings-test' });
        result.textContent = (r && r.ok) ? '已触发 ✓' : ('返回：' + JSON.stringify(r));
      } catch (err) {
        result.textContent = '失败：' + (err && err.message ? err.message : err);
      }
    });
  }

  function bindBrowseRepo() {
    const btn = document.getElementById('browseRepo');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const dir = await window.lumia.pickDirectory();
      if (!dir) return;
      const arr = (getPath(working, 'git.repos') || []).slice();
      if (!arr.includes(dir)) arr.push(dir);
      setPath(working, 'git.repos', arr);
      const container = document.querySelector('[data-list="git.repos"]');
      if (container) renderList(container);
      markDirty();
    });
  }

  function bindGithubAuth() {
    const btn = document.getElementById('ghAuth');
    const result = document.getElementById('ghAuthResult');
    if (!btn) return;
    let polling = null;
    btn.addEventListener('click', async () => {
      if (polling) return;
      const clientId = (getPath(working, 'git.github_client_id') || '').trim();
      result.textContent = '正在向 GitHub 申请授权…';
      let start;
      try {
        start = await window.lumia.postJSON('/api/github/auth/start', { client_id: clientId });
      } catch (err) {
        result.textContent = '失败：' + (err && err.message ? err.message : err);
        return;
      }
      if (!start.ok) { result.textContent = start.reason || '授权启动失败'; return; }
      try { await navigator.clipboard.writeText(start.user_code); } catch (_) {}
      window.lumia.openExternal(start.verification_uri);
      result.textContent = '在打开的网页输入代码 ' + start.user_code + '（已复制），等待授权…';
      const deadline = Date.now() + (start.expires_in || 900) * 1000;
      let interval = Math.max(5, start.interval || 5) * 1000;
      const step = async () => {
        if (Date.now() > deadline) { polling = null; result.textContent = '授权超时，请重试。'; return; }
        let r;
        try {
          r = await window.lumia.postJSON('/api/github/auth/poll', { device_code: start.device_code });
        } catch (_) { polling = setTimeout(step, interval); return; }
        if (r.ok) {
          polling = null;
          setPath(working, 'git.github_username', r.username);
          setPath(baseline, 'git.github_username', r.username);
          try {
            const cfg = await window.lumia.fetchJSON('/api/config');
            const token = getPath(cfg, 'git.github_token') || '';
            setPath(working, 'git.github_token', token);
            setPath(baseline, 'git.github_token', token);
          } catch (_) {}
          renderFields();
          result.textContent = '已授权为 ' + r.username + ' ✓';
          return;
        }
        if (r.pending) {
          if (r.slow_down) interval += 5000;
          polling = setTimeout(step, interval);
          return;
        }
        polling = null;
        result.textContent = r.reason || '授权失败';
      };
      polling = setTimeout(step, interval);
    });
  }

  function bindNav() {
    const items = document.querySelectorAll('.nav-item');
    items.forEach((item) => {
      item.addEventListener('click', () => {
        const target = item.dataset.panel;
        items.forEach((i) => i.classList.toggle('active', i === item));
        document.querySelectorAll('.panel').forEach((p) =>
          p.classList.toggle('active', p.dataset.panel === target));
      });
    });
  }

  const overlay = document.getElementById('saveOverlay');
  const countNum = document.getElementById('countNum');
  const barFill = document.getElementById('overlayBarFill');

  function beginSave() {
    if (!dirty) { setStatus('没有需要保存的更改'); return; }
    let remaining = COUNTDOWN_SECONDS;
    countNum.textContent = remaining;
    barFill.style.transition = 'none';
    barFill.style.width = '100%';
    overlay.classList.add('show');
    requestAnimationFrame(() => {
      barFill.style.transition = 'width ' + COUNTDOWN_SECONDS + 's linear';
      barFill.style.width = '0%';
    });
    countdownTimer = setInterval(() => {
      remaining -= 1;
      countNum.textContent = Math.max(0, remaining);
      if (remaining <= 0) { clearInterval(countdownTimer); countdownTimer = null; commitSave(); }
    }, 1000);
  }

  function cancelSave() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    overlay.classList.remove('show');
    setStatus('已撤销保存，更改仍暂存', 'dirty');
  }

  function applyNow() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    commitSave();
  }

  async function commitSave() {
    overlay.classList.remove('show');
    setStatus('正在保存…');
    try {
      const saved = await window.lumia.putJSON('/api/config', working);
      baseline = clone(saved);
      working = clone(saved);
      dirty = false;
      clearDraft();
      renderFields();
      renderLists();
      setStatus('已保存并生效 ✓', 'ok');
    } catch (err) {
      setStatus('保存失败：' + (err && err.message ? err.message : err), 'dirty');
    }
  }

  function bindSaveReset() {
    document.getElementById('saveBtn').addEventListener('click', beginSave);
    document.getElementById('undoSave').addEventListener('click', cancelSave);
    document.getElementById('applyNow').addEventListener('click', applyNow);
    document.getElementById('resetBtn').addEventListener('click', () => {
      if (countdownTimer) cancelSave();
      working = clone(baseline);
      dirty = false;
      clearDraft();
      renderFields();
      renderLists();
      setStatus('已重置为已保存的设置');
    });
  }

  // ---- pet skin（立即写大脑，并同步 working 避免保存冲掉）----
  async function refreshPetSkin() {
    const status = document.getElementById('petSkinStatus');
    const preview = document.getElementById('petSkinPreview');
    try {
      const list = await window.lumia.fetchJSON('/api/pet/skins');
      const cur = list.current || 'bilibili_face';
      const meta = (list.skins || []).find((s) => s.id === cur);
      if (status) status.textContent = meta ? `${meta.name}（${meta.mode || '?'}）` : cur;
      const radio = document.querySelector(`input[name="petSkin"][value="${cur}"]`);
      if (radio) radio.checked = true;
      if (preview) preview.textContent = JSON.stringify(list, null, 2);
      working.pet = working.pet || {};
      working.pet.skin = cur;
      baseline.pet = baseline.pet || {};
      baseline.pet.skin = cur;
    } catch (_) {
      if (status) status.textContent = '无法读取 /api/pet/skins';
    }
  }

  function bindPetSkin() {
    document.getElementById('petSkinApply')?.addEventListener('click', async () => {
      const picked = document.querySelector('input[name="petSkin"]:checked');
      if (!picked) return;
      try {
        await window.lumia.putJSON('/api/pet/skin', { skin: picked.value });
        await refreshPetSkin();
        setStatus(`外形已切换 → ${picked.value}`, 'ok');
      } catch (e) { alert(e.message); }
    });
    document.getElementById('petSkinRefresh')?.addEventListener('click', refreshPetSkin);
    document.querySelectorAll('.nav-item').forEach((item) => {
      item.addEventListener('click', () => { if (item.dataset.panel === 'petskin') refreshPetSkin(); });
    });
  }

  // ---- pet debug ----
  async function refreshPetDebug() {
    const status = document.getElementById('petDebugStatus');
    const preview = document.getElementById('petDebugPreview');
    try {
      const st = await window.lumia.fetchJSON('/api/pet/state');
      const dbg = st.debug || {};
      status.textContent = dbg.forced
        ? `强制 ${dbg.action} → ${st.action}（至 ${dbg.until || '?'}）`
        : `自动 · 当前 ${st.action}（自然 ${st.natural_action || st.action}）`;
      if (preview) {
        preview.textContent = JSON.stringify({
          action: st.action, skin: st.skin, bubble: st.bubble,
          scale: st.scale, steal_cursor: st.steal_cursor, debug: dbg,
        }, null, 2);
      }
      if (!dbg.forced) document.getElementById('petDebugAction').value = 'auto';
      else document.getElementById('petDebugAction').value = dbg.action || 'auto';
    } catch (_) {
      if (status) status.textContent = '无法读取 /api/pet/state';
    }
  }

  function bindPetDebug() {
    document.getElementById('petDebugRefresh')?.addEventListener('click', refreshPetDebug);
    document.getElementById('petDebugClear')?.addEventListener('click', async () => {
      try { await window.lumia.postJSON('/api/pet/debug', { action: 'auto' }); await refreshPetDebug(); } catch (e) { alert(e.message); }
    });
    document.getElementById('petDebugApply')?.addEventListener('click', async () => {
      const action = document.getElementById('petDebugAction').value;
      const minutes = Number(document.getElementById('petDebugMinutes').value) || 10;
      const bubble = document.getElementById('petDebugBubble').value.trim() || null;
      try {
        await window.lumia.postJSON('/api/pet/debug', { action, minutes, bubble });
        await refreshPetDebug();
      } catch (e) { alert(e.message); }
    });
    document.querySelectorAll('.nav-item').forEach((item) => {
      item.addEventListener('click', () => { if (item.dataset.panel === 'petdebug') refreshPetDebug(); });
    });
  }

  async function init() {
    bindNav();
    bindFields();
    bindListEditors();
    bindChairTest();
    bindBrowseRepo();
    bindGithubAuth();
    bindSaveReset();
    bindPetSkin();
    bindPetDebug();

    try {
      baseline = clone(await window.lumia.fetchJSON('/api/config'));
    } catch (_) {
      baseline = {};
      setStatus('无法连接后端（:8787），仅编辑本地草稿', 'dirty');
    }

    const draft = loadDraft();
    if (draft) {
      working = draft;
      dirty = true;
    } else {
      working = clone(baseline);
    }

    renderFields();
    renderLists();
    if (dirty) setStatus('已恢复未保存的更改', 'dirty');
    else setStatus('已加载');
  }

  init();
})();
