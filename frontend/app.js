const byId = id => document.getElementById(id);
const format = value => new Intl.NumberFormat().format(Number(value) || 0);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const localToday = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
};

let factories = [];
let factoryId = '';
let selectedDay = localToday();
let compareDay = '';
let refreshBusy = false;

async function api(path) {
  const response = await fetch(path, {cache:'no-store'});
  let body = {};
  try { body = await response.json(); } catch {}
  if (!response.ok) throw new Error(body.detail || `API request failed (${response.status})`);
  return body;
}

function factoryBase(suffix) {
  return `/api/factories/${encodeURIComponent(factoryId)}/${suffix}`;
}

function populateFactories() {
  byId('factory-picker').innerHTML = factories.map((factory, index) => {
    const num = index + 1;
    return `<option value="${escapeHtml(factory.factory_id)}">Factory ${num} · ${escapeHtml(factory.name)}</option>`;
  }).join('');
  byId('factory-picker').value = factoryId;
}

async function populateDays() {
  const days = await api(factoryBase('days'));
  const today = localToday();
  if (!days.length) throw new Error('No daily simulator data exists. Run the simulator first.');
  if (!days.includes(selectedDay)) selectedDay = days.includes(today) ? today : days[days.length - 1];
  const options = days.map(day => `<option value="${day}">${day}${day === today ? ' · today' : ''}</option>`).join('');
  byId('day-picker').innerHTML = options;
  byId('day-picker').value = selectedDay;
}

function statusClass(status) {
  if (['down','attention','maintenance'].includes(String(status).toLowerCase())) return 'down';
  if (['idle','off'].includes(String(status).toLowerCase())) return 'idle';
  return 'running';
}

function renderFloor(factory, productionRows, machineRows) {
  const machines = new Map(machineRows.map(machine => [machine.machine_id, machine]));
  const rows = new Map(productionRows.map(row => [row.line_id, row]));
  const allMachines = [];
  const html = (factory.lines || []).map(line => {
    const output = rows.get(line.line_id) || {};
    const achieved = Number(output.achieved_units) || 0;
    const target = Number(output.target_units) || Number(line.daily_target) || 0;
    const percent = target ? Math.min(100, achieved / target * 100) : 0;
    const stageHtml = (line.machines || []).map((machine, index) => {
      const live = machines.get(machine.machine_id) || {status:'running'};
      const state = statusClass(live.status);
      allMachines.push(state);
      const icon = escapeHtml(machine.code || `M${index+1}`);
      return `${index ? '<span class="stage-arrow" aria-hidden="true">›</span>' : ''}<div class="stage is-${state}" title="${escapeHtml(machine.machine_type)} · ${escapeHtml(machine.machine_id)}">
        <span class="stage-icon">${icon}</span><span class="stage-text"><span class="stage-number">STAGE ${String(index+1).padStart(2,'0')} · ${escapeHtml(machine.machine_id)}</span><span class="stage-name">${escapeHtml(machine.machine_type)}</span><span class="stage-state"><i class="status-dot ${state}"></i>${escapeHtml(live.status || 'unknown')}</span></span></div>`;
    }).join('');
    const lineStatus = output.line_status || 'running';
    const fillClass = lineStatus === 'machine_down' || lineStatus === 'material_shortage' ? 'critical' : percent < 80 ? 'warn' : '';
    const shortfall = Math.max(0, target - achieved);
    const hasIssue = shortfall > 0 || lineStatus !== 'running' || percent < 100;
    return `<article class="line-lane">
      <div class="line-top"><div class="line-identity"><span class="line-number">LINE ${escapeHtml(String(line.line_id || '').replace(/^L/i,''))}</span><div class="line-copy"><div class="line-name">${escapeHtml(line.name)}</div><div class="line-purpose">${escapeHtml(line.process_description || `Produces ${line.product_name} through its listed stations.`)}</div><div class="product-name">Finished product · ${escapeHtml(line.product_name)}</div></div></div>
      <div class="line-output"><b>${format(achieved)}</b> <span>/ ${format(target)}</span><small>finished units · ${target ? Math.round(achieved / target * 100) : 0}% of plan</small></div></div>
      <div class="progress-track" aria-label="${Math.round(percent)} percent of target"><div class="progress-fill ${fillClass}" style="width:${percent}%"></div></div>
      <div class="stages-heading"><span>PROCESS ROUTE · 10 WORKSTATIONS</span><span class="scroll-tip" hidden>Scroll horizontally to follow line route →</span></div>
      <div class="stages-scroll"><div class="stages">${stageHtml}</div></div>
      <div class="line-foot">
        <span>${escapeHtml(line.line_id)} · ${escapeHtml(line.product_code || '')}</span>
        <span class="line-status ${escapeHtml(lineStatus)}">${escapeHtml(lineStatus.replaceAll('_',' '))}</span>
      </div>
    </article>`;
  }).join('');
  byId('floor-lines').innerHTML = html || '<div class="empty-state">No production lines are configured.</div>';

  document.querySelectorAll('.line-lane').forEach(lane => {
    const track = lane.querySelector('.stages-scroll');
    const hint = lane.querySelector('.scroll-tip');
    hint.hidden = track.scrollWidth <= track.clientWidth + 2;
  });
  const running = allMachines.filter(status => status === 'running').length;
  byId('running-count').textContent = running;
  byId('down-count').textContent = allMachines.length - running;
  byId('availability').textContent = allMachines.length ? `${Math.round(running / allMachines.length * 100)}%` : '—';
  byId('machine-caption').textContent = `${running} of ${allMachines.length} machines operating`;
}

function renderMetrics(factory, productionRows, inventoryRows, qualityRows) {
  const achieved = productionRows.reduce((sum,row) => sum + (Number(row.achieved_units) || 0), 0);
  const target = productionRows.reduce((sum,row) => sum + (Number(row.target_units) || 0), 0);
  const alerts = inventoryRows.filter(row => ['reorder','out_of_stock'].includes(row.status)).length;
  byId('page-title').textContent = factory.name || 'Factory floor';
  byId('factory-location').textContent = `${factory.industry || 'Factory'} · ${factory.location || ''}`;
  byId('total-output').textContent = format(achieved);
  byId('output-caption').textContent = `finished units · ${selectedDay}`;
  byId('attainment').textContent = `${target ? Math.round(achieved / target * 100) : 0}%`;
  byId('target-caption').textContent = `${format(achieved)} achieved / ${format(target)} planned`;
  byId('material-alerts').textContent = alerts;
  byId('material-caption').textContent = alerts ? 'materials need attention' : 'no materials below reorder level';
  const inspected = qualityRows.reduce((sum, row) => sum + (Number(row.inspected_units) || 0), 0);
  const passed = qualityRows.reduce((sum, row) => sum + (Number(row.passed_units) || 0), 0);
  byId('quality-yield').textContent = inspected ? `${(passed / inspected * 100).toFixed(1)}%` : '—';
  byId('quality-caption').textContent = `${format(passed)} of ${format(inspected)} units passed`;
}

async function loadDashboard() {
  if (refreshBusy || !factoryId || !selectedDay) return;
  refreshBusy = true;
  byId('toast').hidden = true;
  try {
    const factory = await api(`/api/factories/${encodeURIComponent(factoryId)}`);
    const queryDay = `?day=${encodeURIComponent(selectedDay)}`;
    // Fetch real-time operational telemetry for the floor monitor
    const [production, machineData, inventoryData, qualityData] = await Promise.all([
      api(factoryBase(`production${queryDay}`)),
      api(factoryBase(`machines${queryDay}`)),
      api(factoryBase(`inventory${queryDay}`)),
      api(factoryBase(`quality${queryDay}`)),
    ]);
    const productionRows = production.lines || [];
    const machineRows = machineData.machines || [];
    const inventoryRows = inventoryData.materials || [];
    const qualityRows = qualityData.lines || [];
    renderMetrics(factory, productionRows, inventoryRows, qualityRows);
    renderFloor(factory, productionRows, machineRows);
  } catch (error) {
    byId('toast').textContent = `${error.message}. Start the V2 API and simulator data first.`;
    byId('toast').hidden = false;
    byId('floor-lines').innerHTML = '<div class="empty-state">Factory data could not be loaded.</div>';
  } finally {
    refreshBusy = false;
  }
}

async function initialize() {
  try {
    factories = await api('/api/factories');
    if (!factories.length) throw new Error('No simulator factories found. Run the simulator first.');
    factoryId = factories[0].factory_id;
    populateFactories();
    await populateDays();
    await loadDashboard();
  } catch (error) {
    byId('toast').textContent = `${error.message}. Start the V2 API and simulator data first.`;
    byId('toast').hidden = false;
    byId('floor-lines').innerHTML = '<div class="empty-state">Cannot connect to the local FactoryOS API.</div>';
  }
}

byId('factory-picker').addEventListener('change', async event => {
  factoryId = event.target.value;
  selectedDay = localToday();
  try { await populateDays(); await loadDashboard(); }
  catch (error) { byId('toast').textContent = error.message; byId('toast').hidden = false; }
});
byId('day-picker').addEventListener('change', async event => {
  selectedDay = event.target.value;
  await loadDashboard();
});

initialize();
window.setInterval(async () => {
  if (!factoryId || refreshBusy) return;
  try { await populateDays(); await loadDashboard(); }
  catch (error) { console.warn('Dashboard refresh failed:', error); }
}, 10000);

// =====================================================================
// AI Operations Copilot Slide-over Controller
// =====================================================================

function initCopilot() {
  const drawer = byId('copilot-drawer');
  const backdrop = byId('copilot-backdrop');
  const topbarBtn = byId('topbar-copilot-btn');
  const sidebarBtn = byId('sidebar-copilot-btn');
  const closeBtn = byId('close-copilot-btn');
  const queryInput = byId('agent-query-input');
  const queryBtn = byId('agent-query-btn');
  const copilotForm = byId('copilot-form');
  const multiToggle = byId('agent-multi-toggle');
  const modeLabel = byId('agent-mode-label');
  const welcomeBox = byId('copilot-welcome');
  const resultsBox = byId('agent-results');

  if (!drawer || !queryInput) return;

  function openCopilot() {
    drawer.hidden = false;
    backdrop.hidden = false;
    document.body.style.overflow = 'hidden';
    setTimeout(() => queryInput.focus(), 80);
  }

  function closeCopilot() {
    drawer.hidden = true;
    backdrop.hidden = true;
    document.body.style.overflow = '';
  }

  function toggleCopilot() {
    if (drawer.hidden) openCopilot();
    else closeCopilot();
  }

  window.triggerCopilot = function(prompt) {
    openCopilot();
    if (prompt) {
      queryInput.value = prompt;
      runAgentInvestigation();
    }
  };

  // Open & Close triggers
  if (topbarBtn) topbarBtn.addEventListener('click', openCopilot);
  if (sidebarBtn) sidebarBtn.addEventListener('click', openCopilot);
  if (closeBtn) closeBtn.addEventListener('click', closeCopilot);
  if (backdrop) backdrop.addEventListener('click', closeCopilot);
  const floatingMachoBtn = byId('floating-macho-btn');
  if (floatingMachoBtn) floatingMachoBtn.addEventListener('click', openCopilot);

  // Keyboard shortcut Ctrl+K / Cmd+K and Escape
  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      toggleCopilot();
    } else if (e.key === 'Escape' && !drawer.hidden) {
      closeCopilot();
    }
  });

  // Toggle mode label
  if (multiToggle) {
    multiToggle.addEventListener('change', () => {
      modeLabel.textContent = multiToggle.checked ? 'Multi-Agent' : 'Single-Agent';
    });
  }

  // Suggestion card clicks
  document.querySelectorAll('.suggestion-card').forEach(card => {
    card.addEventListener('click', () => {
      queryInput.value = card.dataset.prompt;
      runAgentInvestigation();
    });
  });

  // Form submit
  if (copilotForm) {
    copilotForm.addEventListener('submit', e => {
      e.preventDefault();
      runAgentInvestigation();
    });
  }

  async function runAgentInvestigation() {
    const question = (queryInput.value || '').trim();
    if (!question) {
      queryInput.focus();
      return;
    }

    const isMulti = multiToggle ? multiToggle.checked : false;
    queryBtn.disabled = true;
    queryInput.disabled = true;
    queryBtn.querySelector('.btn-text').textContent = 'Macho Analyzing...';

    // Update UI state
    byId('agent-query-echo-text').textContent = question;
    welcomeBox.hidden = true;
    resultsBox.hidden = false;
    byId('agent-badge-mode').textContent = isMulti ? 'Multi-Agent Supervisor' : 'Single-Agent';
    byId('agent-response-text').innerHTML = '<div class="loading">Macho investigating telemetry & operational logs…</div>';

    const startTime = performance.now();

    try {
      const response = await fetch('/api/agent/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          factory_id: factoryId || 'F001',
          multi_agent: isMulti
        })
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `Agent query failed (HTTP ${response.status})`);
      }

      const data = await response.json();
      const elapsedMs = (performance.now() - startTime) / 1000;

      renderCopilotResults(data, elapsedMs, isMulti);
    } catch (err) {
      byId('agent-response-text').innerHTML = `<div style="color:#ec776b;padding:8px 0;"><strong>Macho Investigation Error:</strong> ${escapeHtml(err.message)}</div>`;
    } finally {
      queryBtn.disabled = false;
      queryInput.disabled = false;
      queryBtn.querySelector('.btn-text').textContent = 'Query Macho';
      queryInput.value = '';
      queryInput.focus();
    }
  }

  function formatMarkdown(text) {
    if (!text) return 'No findings returned.';
    let html = escapeHtml(text);
    // Bold **text**
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Headers ###
    html = html.replace(/^### (.*$)/gim, '<h4 style="margin:12px 0 4px;color:#a3dec2;">$1</h4>');
    // Headers ##
    html = html.replace(/^## (.*$)/gim, '<h3 style="margin:14px 0 6px;color:#8be3b8;border-bottom:1px solid #22372d;padding-bottom:3px;">$1</h3>');
    // Headers #
    html = html.replace(/^# (.*$)/gim, '<h2 style="margin:16px 0 8px;color:#7ee0af;">$1</h2>');
    // Citations [E1]
    html = html.replace(/\[(E\d+)\]/g, '<span class="citation-pill">[$1]</span>');
    // Bullets
    html = html.replace(/^\s*[-*]\s+(.*$)/gim, '<li style="margin-left:16px;line-height:1.7;">$1</li>');
    // Line breaks
    html = html.replace(/\n\n/g, '<br><br>');
    return html;
  }

  function renderCopilotResults(data, elapsedSec, isMulti) {
    byId('agent-badge-mode').textContent = isMulti ? 'Multi-Agent Supervisor' : 'Single-Agent';
    byId('agent-meta-time').textContent = `⏱️ ${elapsedSec.toFixed(2)}s`;

    const evidenceList = data.evidence || [];
    byId('agent-meta-citations').textContent = `🔖 ${evidenceList.length} facts logged`;
    byId('evidence-count-tag').textContent = evidenceList.length;

    // Render formatted answer
    const rawAnswer = data.final_answer || data.synthesized_answer || 'No response returned.';
    byId('agent-response-text').innerHTML = formatMarkdown(rawAnswer);

    // Render Evidence drawer items
    const evidenceContainer = byId('agent-evidence-list');
    if (!evidenceList.length) {
      evidenceContainer.innerHTML = '<div class="empty-state">No direct evidence facts registered.</div>';
    } else {
      evidenceContainer.innerHTML = evidenceList.map(ev => `
        <div class="evidence-item">
          <span class="evidence-id-tag">[${escapeHtml(ev.evidence_id || 'E?')}]</span>
          <strong>${escapeHtml(ev.tool || 'tool')}</strong>: 
          <span>${escapeHtml(typeof ev.content === 'object' ? JSON.stringify(ev.content) : ev.content)}</span>
        </div>
      `).join('');
    }

    // Render Trace drawer items
    const traceList = data.trace || [];
    const traceContainer = byId('agent-trace-list');
    byId('trace-count-tag').textContent = traceList.length;
    if (!traceList.length) {
      traceContainer.innerHTML = '<div class="empty-state">No trace steps recorded.</div>';
    } else {
      traceContainer.innerHTML = traceList.map(t => `
        <div class="trace-item ${escapeHtml(t.type || '')}">
          <small style="color:#788b81;">[R${t.round ?? 0} · ${escapeHtml(t.type || '')}]</small> 
          <div>${escapeHtml(typeof t.content === 'object' ? JSON.stringify(t.content) : t.content)}</div>
        </div>
      `).join('');
    }

    // Smooth scroll down to response
    resultsBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// Initialize Copilot on load
document.addEventListener('DOMContentLoaded', initCopilot);
if (document.readyState !== 'loading') initCopilot();
