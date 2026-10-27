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
let employeesData = null;

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
    return `<option value="${escapeHtml(factory.factory_id)}" title="${escapeHtml(factory.name)}">F${num}</option>`;
  }).join('');
  byId('factory-picker').value = factoryId;
}

async function populateDays() {
  const days = await api(factoryBase('days'));
  const today = localToday();
  if (!days.length) throw new Error('No daily simulator data exists. Run the simulator first.');
  if (!days.includes(selectedDay)) selectedDay = days.includes(today) ? today : days[days.length - 1];
  const options = days.map(day => `<option value="${day}">${day}${day === today ? ' (today)' : ''}</option>`).join('');
  byId('day-picker').innerHTML = options;
  byId('day-picker').value = selectedDay;
}

function statusClass(status) {
  if (['down','attention','maintenance','unstaffed'].includes(String(status).toLowerCase())) return 'down';
  if (['idle','off'].includes(String(status).toLowerCase())) return 'idle';
  return 'running';
}

function renderFloor(factory, productionRows, machineRows, empData, ordersData) {
  const machines = new Map(machineRows.map(machine => [machine.machine_id, machine]));
  const rows = new Map(productionRows.map(row => [row.line_id, row]));
  const machineCoverage = empData?.machine_coverage || {};
  const allMachines = [];
  const html = (factory.lines || []).map(line => {
    const output = rows.get(line.line_id) || {};
    const achieved = Number(output.achieved_units) || 0;
    const target = Number(output.target_units) || Number(line.daily_target) || 0;
    const percent = target ? Math.min(100, achieved / target * 100) : 0;

    // Operation-level workstation grouping & bottleneck detection
    const opMap = new Map();
    (line.machines || []).forEach(m => {
      const type = m.machine_type;
      if (!opMap.has(type)) opMap.set(type, []);
      opMap.get(type).push(m);
    });

    const haltedBottlenecks = []; // Operator absent / unstaffed bottlenecks halting line
    const haltedMachineDowns = []; // Machine hardware failure halting line
    const degradedBottlenecks = [];
    const degradedMachineDowns = [];

    opMap.forEach((wsList, opType) => {
      const total = wsList.length;
      let unstaffedCount = 0;
      let machineDownCount = 0;
      let activeCount = 0;

      wsList.forEach(m => {
        const live = machines.get(m.machine_id) || { status: 'running' };
        const cov = machineCoverage[m.machine_id];
        const isMStaffed = cov ? Boolean(cov.is_staffed) : (live.status !== 'unstaffed');
        const isMHardwareDown = live.status === 'down';
        if (!isMStaffed) {
          unstaffedCount++;
        } else if (isMHardwareDown) {
          machineDownCount++;
        } else {
          activeCount++;
        }
      });

      if (activeCount === 0) {
        if (unstaffedCount > 0) {
          haltedBottlenecks.push({ opType, total, active: activeCount, unstaffedCount });
        }
        if (machineDownCount > 0) {
          haltedMachineDowns.push({ opType, total, active: activeCount, machineDownCount });
        }
      } else if (activeCount < total) {
        if (unstaffedCount > 0) {
          degradedBottlenecks.push({ opType, total, active: activeCount, unstaffedCount });
        }
        if (machineDownCount > 0) {
          degradedMachineDowns.push({ opType, total, active: activeCount, machineDownCount });
        }
      }
    });

    const isLineHalted = haltedBottlenecks.length > 0 || haltedMachineDowns.length > 0;
    const isLineDegraded = !isLineHalted && (degradedBottlenecks.length > 0 || degradedMachineDowns.length > 0);

    const stageHtml = (line.machines || []).map((machine, index) => {
      const live = machines.get(machine.machine_id) || {status:'running'};
      const icon = escapeHtml(machine.code || `M${index+1}`);

      // Workstation stage bottleneck annotation
      const wsList = opMap.get(machine.machine_type) || [machine];
      const totalWs = wsList.length;
      const wsIndex = wsList.findIndex(m => m.machine_id === machine.machine_id) + 1;
      const activeWs = wsList.filter(m => {
        const l = machines.get(m.machine_id) || { status: 'running' };
        const c = machineCoverage[m.machine_id];
        const isStaffed = c ? Boolean(c.is_staffed) : (l.status !== 'unstaffed');
        const isHardwareDown = l.status === 'down';
        return isStaffed && !isHardwareDown;
      }).length;

      const cov = machineCoverage[machine.machine_id];
      const isStaffed = cov ? Boolean(cov.is_staffed) : (live.status !== 'unstaffed');
      const isUnstaffed = !isStaffed;
      const isHardwareDown = live.status === 'down';
      const effectiveState = isUnstaffed ? 'down' : (isHardwareDown ? 'down' : 'running');
      allMachines.push(effectiveState);

      let stageBottleneckBadge = '';
      let stageCardExtraClass = '';

      if (isUnstaffed) {
        stageCardExtraClass = 'stage-unstaffed';
        if (activeWs === 0) {
          stageCardExtraClass += ' stage-critical-bottleneck';
          stageBottleneckBadge = `<span class="stage-bottleneck-flag is-unstaffed-flag" title="Workstation operator absent with no floater assigned. Staffing bottleneck halting line."><i class="flag-dot"></i> BOTTLENECK · OPERATOR ABSENT (LINE HALTED)</span>`;
        } else {
          stageCardExtraClass += ' stage-degraded-bottleneck';
          stageBottleneckBadge = `<span class="stage-bottleneck-flag is-degraded" title="Workstation operator absent. Companion workstation active, line running at reduced speed."><i class="flag-dot"></i> SLOWING DOWN · OPERATOR ABSENT (${activeWs}/${totalWs} WS)</span>`;
        }
      } else if (isHardwareDown) {
        if (activeWs === 0) {
          stageCardExtraClass += ' stage-critical-bottleneck';
          stageBottleneckBadge = `<span class="stage-bottleneck-flag is-critical" title="Zero active workstations for this stage. Machine breakdown, line halted."><i class="flag-dot"></i> MACHINE DOWN · LINE HALTED (${totalWs === 1 ? '1/1 WS DOWN' : 'ALL ' + totalWs + ' DOWN'})</span>`;
        } else {
          stageCardExtraClass += ' stage-degraded-bottleneck';
          stageBottleneckBadge = `<span class="stage-bottleneck-flag is-degraded" title="Workstation machine down. Companion workstation active, line running at reduced speed."><i class="flag-dot"></i> SLOWING DOWN · MACHINE DOWN (${activeWs}/${totalWs} WS RUNNING)</span>`;
        }
      } else if (totalWs > 1 && activeWs < totalWs) {
        stageBottleneckBadge = `<span class="stage-bottleneck-flag is-sister-active" title="Active workstation keeping line running"><i class="flag-dot"></i> ACTIVE STATION (${activeWs}/${totalWs} WS RUNNING)</span>`;
      }

      const wsPill = totalWs > 1 
        ? `<span class="ws-indicator-pill" title="Operation has ${totalWs} parallel workstations">WS ${wsIndex}/${totalWs}</span>` 
        : `<span class="ws-indicator-single" title="Single workstation operation (SPOF bottleneck)">WS 1/1</span>`;

      let operatorCardHtml = '';
      if (cov) {
        if (isUnstaffed) {
          operatorCardHtml = `
            <div class="ws-operator-panel is-absent">
              <div class="ws-operator-meta">
                <span class="ws-op-title">OPERATOR:</span>
                <span class="ws-op-name is-absent" title="${escapeHtml(cov.primary_operator?.name || 'Assigned')} (${escapeHtml(cov.primary_operator?.attendance_note || 'Absent')})">${escapeHtml(cov.primary_operator?.name || 'Assigned')} (ABSENT)</span>
              </div>
              <button type="button" class="btn-assign-floater" data-machine-id="${escapeHtml(machine.machine_id)}" onclick="window.handleOpenAssignModal('${escapeHtml(machine.machine_id)}')">
                Assign Floater ▾
              </button>
            </div>
          `;
        } else if (cov.is_replacement && cov.active_operator) {
          operatorCardHtml = `
            <div class="ws-operator-panel is-replacement">
              <div class="ws-operator-meta">
                <span class="ws-op-title">FLOATER RELIEF:</span>
                <span class="ws-op-name is-relief" title="${escapeHtml(cov.active_operator.name)} (${escapeHtml(cov.active_operator.employee_id)}) covering for absent ${escapeHtml(cov.primary_operator?.name || '')}">${escapeHtml(cov.active_operator.name)}</span>
              </div>
              <button type="button" class="btn-unassign-floater" title="Release floater back to Free Pool" data-machine-id="${escapeHtml(machine.machine_id)}" onclick="window.handleUnassignOperator('${escapeHtml(machine.machine_id)}')">✕ Release</button>
            </div>
          `;
        } else if (cov.active_operator) {
          operatorCardHtml = `
            <div class="ws-operator-panel is-staffed">
              <div class="ws-operator-meta">
                <span class="ws-op-title">OPERATOR:</span>
                <span class="ws-op-name" title="${escapeHtml(cov.active_operator.name)} (${escapeHtml(cov.active_operator.employee_id)}) · Skill: ${escapeHtml(cov.required_skill)}">${escapeHtml(cov.active_operator.name)}</span>
              </div>
            </div>
          `;
        }
      }

      return `${index ? '<span class="stage-arrow" aria-hidden="true">›</span>' : ''}<div class="stage is-${effectiveState} ${stageCardExtraClass}" title="${escapeHtml(machine.machine_type)} · ${escapeHtml(machine.machine_id)} · ${totalWs > 1 ? totalWs + ' parallel workstations' : 'Single workstation'}">
        <span class="stage-icon">${icon}</span><span class="stage-text"><span class="stage-number">STAGE ${String(index+1).padStart(2,'0')} · ${escapeHtml(machine.machine_id)} ${wsPill}</span><span class="stage-name">${escapeHtml(machine.machine_type)}</span><span class="stage-state"><i class="status-dot ${effectiveState}"></i>${isUnstaffed ? 'unstaffed (absent)' : (isHardwareDown ? 'machine down' : 'running')}</span>${stageBottleneckBadge}${operatorCardHtml}</span></div>`;
    }).join('');

    // Real-time Dynamic Work Order tracking per line and day
    const completedOrder = (ordersData?.completed_orders || {})[line.line_id] || null;
    const activeOrder = (ordersData?.active_orders || {})[line.line_id] || null;
    const nextOrder = (ordersData?.next_orders || {})[line.line_id] || null;

    const numSuffix = String(line.line_id || '1').replace(/^L0?/i, '') || '1';
    const defaultOrderId = `ORD-00${numSuffix}`;
    const orderId = activeOrder?.order_id || defaultOrderId;
    const customerName = activeOrder?.customer || 'Industrial OEM Partner';
    const productName = (activeOrder?.product_name || line.product_name || 'Component').replace(/^Finished\s+/i, '');
    const lineDisplayName = (line.name || productName).replace(/^Finished\s+/i, '');

    const isOrderCompleted = activeOrder?.status === 'completed' || (target > 0 && achieved >= target);

    // Active batch vs Completed batch distinction:
    // Only treat as a new order loaded on this shift if an order actually completed TODAY:
    const isOrderCompletedToday = Boolean(completedOrder && completedOrder.completion_date === output.date);
    const isNewBatchLoaded = Boolean(isOrderCompletedToday && activeOrder && activeOrder.order_id !== completedOrder.order_id);
    const activeAchieved = isNewBatchLoaded 
      ? Math.max(Number(activeOrder?.units_completed_current_shift || 0), Number(output.active_batch_units || 0)) 
      : achieved;
    const activeTarget = Number(target || line.daily_target || 960);
    const totalContractUnits = Number(activeOrder?.total_order_units || 0);
    const cumCompletedUnits = Math.max(Number(activeOrder?.cumulative_units_completed || activeOrder?.units_completed || 0), activeAchieved);
    const remUnits = Math.max(0, totalContractUnits - cumCompletedUnits);
    const batchProgressPct = totalContractUnits ? Math.min(100, Math.round((cumCompletedUnits / totalContractUnits) * 100)) : 0;
    const estDaysLeft = activeOrder?.estimated_days_remaining ?? (target ? Math.ceil(remUnits / target) : 0);
    const estFinishDate = activeOrder?.estimated_completion_date || activeOrder?.due_date || '';
    const isScheduleHealthy = activeOrder?.schedule_health !== 'delayed';
    const activePercent = activeTarget ? Math.min(100, Math.round((activeAchieved / activeTarget) * 100)) : 0;

    const lineStatus = output.line_status || 'running';
    let lineStatusDisplay = lineStatus;
    if (isNewBatchLoaded && !isLineHalted && !isLineDegraded) {
      lineStatusDisplay = 'running_new_batch';
    }

    const fillClass = (isLineHalted || lineStatus === 'machine_down' || lineStatus === 'material_shortage' || lineStatus === 'operator_shortage')
      ? 'critical'
      : isLineDegraded
        ? 'warn'
        : isNewBatchLoaded
          ? 'running-active-batch'
          : '';
    const shortfall = Math.max(0, target - achieved);
    const hasIssue = shortfall > 0 || lineStatus !== 'running' || percent < 100;

    // Line header alert pills distinguishing Machine Down from Staffing Bottleneck
    const alertPills = [];
    haltedBottlenecks.forEach(b => {
      alertPills.push(`<span class="bottleneck-pill is-unstaffed-bottleneck" title="Stage ${escapeHtml(b.opType)}: Operator absent with no floater assigned. Staffing bottleneck halting line.">
        <i class="bottleneck-pulse-dot"></i> BOTTLENECK: ${escapeHtml(b.opType).toUpperCase()} (OPERATOR ABSENT)
      </span>`);
    });
    haltedMachineDowns.forEach(m => {
      alertPills.push(`<span class="bottleneck-pill is-critical" title="Stage ${escapeHtml(m.opType)}: Machine breakdown. Line halted.">
        <i class="bottleneck-pulse-dot"></i> MACHINE DOWN: ${escapeHtml(m.opType).toUpperCase()} (LINE HALTED)
      </span>`);
    });

    if (!alertPills.length) {
      degradedBottlenecks.forEach(b => {
        const capPct = Math.round(b.active / b.total * 100);
        alertPills.push(`<span class="bottleneck-pill is-degraded" title="Stage ${escapeHtml(b.opType)}: Operator absent. Line running at ${capPct}% speed.">
          <i class="bottleneck-pulse-dot"></i> SLOWED: ${escapeHtml(b.opType).toUpperCase()} (OPERATOR ABSENT · ${capPct}% SPEED)
        </span>`);
      });
      degradedMachineDowns.forEach(m => {
        const capPct = Math.round(m.active / m.total * 100);
        alertPills.push(`<span class="bottleneck-pill is-degraded" title="Stage ${escapeHtml(m.opType)}: Machine down. Line running at ${capPct}% speed.">
          <i class="bottleneck-pulse-dot"></i> SLOWED: ${escapeHtml(m.opType).toUpperCase()} (${capPct}% SPEED)
        </span>`);
      });
    }

    const lineBottleneckPill = alertPills.join(' ');

    return `<article class="line-lane">
      <div class="line-top">
        <div class="line-identity">
          <span class="line-number ${isLineHalted ? 'is-halted' : (isLineDegraded ? 'is-slowed' : '')}">LINE ${escapeHtml(String(line.line_id || '').replace(/^L/i,''))}</span>
          ${lineBottleneckPill ? `<div class="line-pill-near-number">${lineBottleneckPill}</div>` : ''}
          <div class="line-copy">
            <div class="line-header-row">
              <span class="line-name">${escapeHtml(lineDisplayName)}</span>
              ${isOrderCompleted && !isNewBatchLoaded ? `
                <span class="order-pill is-completed-badge" title="${escapeHtml(customerName)} · Batch quota achieved (100%)">
                  <i class="completed-check-icon">✓</i> Completed: ${escapeHtml(orderId)}
                </span>
                ${nextOrder ? `
                  <button type="button" class="btn-dispatch-next-order" title="Dispatch and load next queued work order onto Line ${escapeHtml(line.line_id)}" onclick="window.handleDispatchNextOrder('${escapeHtml(line.line_id)}', '${escapeHtml(nextOrder.order_id)}')">
                    ▶ Load: ${escapeHtml(nextOrder.order_id)} (${escapeHtml(nextOrder.product_name || 'Next Batch')})
                  </button>
                ` : ''}
              ` : `
                <span class="order-pill is-active" title="${escapeHtml(customerName)} · Due: ${escapeHtml(activeOrder?.due_date || 'In Schedule')}">
                  Order: ${escapeHtml(orderId)}
                </span>
              `}
            </div>
          </div>
        </div>
        <div class="line-output">
          <div class="active-output-wrap">
            <div class="today-output-row">
              <b>${format(activeAchieved)}</b> <span>/ ${format(activeTarget)}</span>
              <span class="today-unit-label">today</span>
            </div>
          </div>
        </div>
      </div>
      <div class="progress-track" aria-label="${Math.round(activePercent)} percent of target"><div class="progress-fill ${fillClass}" style="width:${activePercent}%"></div></div>
      <div class="stages-heading"><span>PROCESS ROUTE · 10 WORKSTATIONS</span><span class="scroll-tip" hidden>Scroll horizontally to follow line route →</span></div>
      <div class="stages-scroll"><div class="stages">${stageHtml}</div></div>
      <div class="line-foot">
        <div class="line-foot-left">
          <span class="foot-order-info is-current-loaded"><b>Active Order:</b> ${escapeHtml(orderId)} (${escapeHtml(productName)})</span>
          <span class="foot-dot">•</span>
          <span class="foot-customer-info"><b>Customer:</b> ${escapeHtml(customerName)}</span>
          ${totalContractUnits ? `
            <span class="foot-dot">•</span>
            <span class="foot-contract-info"><b>Contract:</b> ${format(totalContractUnits)} units (~${estDaysLeft}d left · Due ${escapeHtml(activeOrder?.due_date || 'TBD')})</span>
          ` : ''}
          ${haltedBottlenecks.map(b => `<span class="foot-bottleneck-msg is-critical"><span class="foot-dot">•</span> <b>Bottleneck:</b> ${escapeHtml(b.opType)} (Operator absent · line halted)</span>`).join('')}
          ${haltedMachineDowns.map(m => `<span class="foot-bottleneck-msg is-critical"><span class="foot-dot">•</span> <b>Machine Down:</b> ${escapeHtml(m.opType)} (Equipment breakdown · line halted)</span>`).join('')}
          ${(!isLineHalted && isLineDegraded) ? `<span class="foot-bottleneck-msg is-degraded"><span class="foot-dot">•</span> <b>Line slowed down:</b> Operating at reduced capacity (${degradedBottlenecks.map(b=>b.opType).concat(degradedMachineDowns.map(m=>m.opType)).join(', ')})</span>` : ''}
        </div>
        <span class="line-status ${escapeHtml(lineStatusDisplay)}">${isLineHalted ? (haltedMachineDowns.length && haltedBottlenecks.length ? 'HALTED · DUAL ISSUE' : (haltedBottlenecks.length ? 'BOTTLENECK · OPERATOR ABSENT' : 'MACHINE DOWN · HALTED')) : (isLineDegraded ? 'RUNNING (SLOWED)' : (lineStatusDisplay === 'running_new_batch' ? 'RUNNING (NEW ORDER LOADED)' : escapeHtml(lineStatusDisplay.replaceAll('_',' '))))}</span>
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
  const runningEl = byId('running-count');
  if (runningEl) runningEl.textContent = running;
  const downEl = byId('down-count');
  if (downEl) downEl.textContent = allMachines.length - running;
  const availEl = byId('availability');
  if (availEl) availEl.textContent = allMachines.length ? `${Math.round(running / allMachines.length * 100)}%` : '—';
  const machCapEl = byId('machine-caption');
  if (machCapEl) machCapEl.textContent = `${running} of ${allMachines.length} machines operating`;
}

function renderMetrics(factory, productionRows, inventoryRows, qualityRows, empData) {
  const achieved = productionRows.reduce((sum,row) => sum + (Number(row.achieved_units) || 0), 0);
  const target = productionRows.reduce((sum,row) => sum + (Number(row.target_units) || 0), 0);
  const alerts = inventoryRows.filter(row => ['reorder','out_of_stock'].includes(row.status)).length;

  // Update Plant Identity Banner
  const plantTitleEl = byId('plant-title');
  if (plantTitleEl) plantTitleEl.textContent = factory.name || 'Factory Operations Floor';
  const plantCodeEl = byId('plant-code-pill');
  if (plantCodeEl) {
    const num = (factory.factory_id || 'F1').match(/\d+/);
    plantCodeEl.textContent = num ? `F${parseInt(num[0], 10)}` : (factory.factory_code || 'F1');
  }
  const plantIndEl = byId('plant-industry');
  if (plantIndEl) plantIndEl.textContent = factory.industry ? factory.industry.toUpperCase() : 'MANUFACTURING';
  const plantLocEl = byId('plant-location');
  if (plantLocEl) plantLocEl.textContent = factory.location || '';
  const plantLinesEl = byId('plant-lines-count');
  if (plantLinesEl) plantLinesEl.textContent = `${(factory.lines || []).length} Active Production Lines`;

  // Update Plant Crew Roster Capsule
  const sum = empData?.summary || {};
  const attEl = byId('roster-attendance-rate');
  if (attEl) attEl.textContent = sum.attendance_pct || '—';
  const attDetEl = byId('roster-attendance-detail');
  if (attDetEl) attDetEl.textContent = sum.total_roster ? `${sum.present_count || 0}/${sum.total_roster} Present` : 'Shift A Roster';
  const fpEl = byId('roster-freepool-count');
  if (fpEl) fpEl.textContent = `${sum.free_pool_count ?? 0} Floaters`;
  const unstaffEl = byId('roster-unstaffed-count');
  if (unstaffEl) unstaffEl.textContent = `${sum.unstaffed_count ?? 0} Stations`;
  const unstaffDetEl = byId('roster-unstaffed-detail');
  if (unstaffDetEl) unstaffDetEl.textContent = sum.unstaffed_count ? 'Awaiting floater' : 'Fully staffed';

  const totalOutEl = byId('total-output');
  if (totalOutEl) totalOutEl.textContent = format(achieved);
  const outCapEl = byId('output-caption');
  if (outCapEl) outCapEl.textContent = `finished units · ${selectedDay}`;
  const attainEl = byId('attainment');
  if (attainEl) attainEl.textContent = `${target ? Math.round(achieved / target * 100) : 0}%`;
  const targetCapEl = byId('target-caption');
  if (targetCapEl) targetCapEl.textContent = `${format(achieved)} achieved / ${format(target)} planned`;
  const matAlertsEl = byId('material-alerts');
  if (matAlertsEl) matAlertsEl.textContent = alerts;
  const matCapEl = byId('material-caption');
  if (matCapEl) matCapEl.textContent = alerts ? 'materials need attention' : 'no materials below reorder level';
  const inspected = qualityRows.reduce((sum, row) => sum + (Number(row.inspected_units) || 0), 0);
  const passed = qualityRows.reduce((sum, row) => sum + (Number(row.passed_units) || 0), 0);
  const yieldEl = byId('quality-yield');
  if (yieldEl) yieldEl.textContent = inspected ? `${(passed / inspected * 100).toFixed(1)}%` : '—';
  const qualCapEl = byId('quality-caption');
  if (qualCapEl) qualCapEl.textContent = `${format(passed)} of ${format(inspected)} units passed`;
}

async function loadDashboard() {
  if (refreshBusy || !factoryId || !selectedDay) return;
  refreshBusy = true;
  byId('toast').hidden = true;
  try {
    const factory = await api(`/api/factories/${encodeURIComponent(factoryId)}`);
    const queryDay = `?day=${encodeURIComponent(selectedDay)}`;
    // Fetch real-time operational telemetry, workforce staffing, and work orders
    const [production, machineData, inventoryData, qualityData, empData, ordersData] = await Promise.all([
      api(factoryBase(`production${queryDay}`)),
      api(factoryBase(`machines${queryDay}`)),
      api(factoryBase(`inventory${queryDay}`)),
      api(factoryBase(`quality${queryDay}`)),
      api(factoryBase('employees')).catch(() => null),
      api(factoryBase(`orders${queryDay}`)).catch(() => ({ active_orders: {}, next_orders: {} })),
    ]);
    employeesData = empData || { summary: {}, machine_coverage: {}, free_pool: [], absent: [] };
    const productionRows = production.lines || [];
    const machineRows = machineData.machines || [];
    const inventoryRows = inventoryData.materials || [];
    const qualityRows = qualityData.lines || [];
    renderMetrics(factory, productionRows, inventoryRows, qualityRows, employeesData);
    renderFloor(factory, productionRows, machineRows, employeesData, ordersData);
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

let _copilotInitialized = false;
let _agentQueryInProgress = false;
let _chatHistory = [];

function initCopilot() {
  if (_copilotInitialized) return;
  _copilotInitialized = true;

  const drawer = byId('copilot-drawer');
  const backdrop = byId('copilot-backdrop');
  const topbarBtn = byId('topbar-copilot-btn');
  const sidebarBtn = byId('sidebar-copilot-btn');
  const closeBtn = byId('close-copilot-btn');
  const clearBtn = byId('clear-copilot-btn');
  const queryInput = byId('agent-query-input');
  const queryBtn = byId('agent-query-btn');
  const copilotForm = byId('copilot-form');
  const multiToggle = byId('agent-multi-toggle');
  const chatBody = byId('copilot-chat-body');
  const chatIntro = byId('copilot-intro');
  const chatStream = byId('copilot-chat-stream');

  if (!drawer || !queryInput) return;

  function resetCopilotState() {
    _chatHistory = [];
    if (chatIntro) chatIntro.style.display = 'block';
    if (chatStream) chatStream.innerHTML = '';
    if (queryInput) {
      queryInput.value = '';
      queryInput.focus();
    }
  }

  function openCopilot() {
    drawer.hidden = false;
    drawer.removeAttribute('hidden');
    backdrop.hidden = false;
    backdrop.removeAttribute('hidden');
    setTimeout(() => queryInput.focus(), 80);
  }

  function closeCopilot() {
    drawer.hidden = true;
    drawer.setAttribute('hidden', '');
    backdrop.hidden = true;
    backdrop.setAttribute('hidden', '');
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
  if (clearBtn) clearBtn.addEventListener('click', resetCopilotState);
  if (backdrop) backdrop.addEventListener('click', closeCopilot);

  // Draggable Macho Robot Companion logic
  const machoWidget = byId('macho-robot-widget');
  const floatingMachoBtn = byId('floating-macho-btn');
  let isDraggingWidget = false;
  let dragHasMoved = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let widgetInitialLeft = 0;
  let widgetInitialTop = 0;

  // Restore saved position if available
  try {
    const savedPos = JSON.parse(localStorage.getItem('macho_widget_pos') || 'null');
    if (savedPos && machoWidget) {
      machoWidget.style.left = savedPos.left;
      machoWidget.style.top = savedPos.top;
      machoWidget.style.right = 'auto';
      machoWidget.style.bottom = 'auto';
    }
  } catch {}

  if (machoWidget) {
    machoWidget.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      isDraggingWidget = true;
      dragHasMoved = false;
      dragStartX = e.clientX;
      dragStartY = e.clientY;

      const rect = machoWidget.getBoundingClientRect();
      widgetInitialLeft = rect.left;
      widgetInitialTop = rect.top;

      try { machoWidget.setPointerCapture(e.pointerId); } catch {}
    });

    machoWidget.addEventListener('pointermove', e => {
      if (!isDraggingWidget) return;
      const dx = e.clientX - dragStartX;
      const dy = e.clientY - dragStartY;

      if (!dragHasMoved && Math.hypot(dx, dy) > 4) {
        dragHasMoved = true;
        machoWidget.classList.add('is-dragging');
      }

      if (dragHasMoved) {
        const rect = machoWidget.getBoundingClientRect();
        const maxX = window.innerWidth - rect.width - 12;
        const maxY = window.innerHeight - rect.height - 12;
        const newLeft = Math.max(12, Math.min(maxX, widgetInitialLeft + dx));
        const newTop = Math.max(12, Math.min(maxY, widgetInitialTop + dy));

        machoWidget.style.left = `${newLeft}px`;
        machoWidget.style.top = `${newTop}px`;
        machoWidget.style.right = 'auto';
        machoWidget.style.bottom = 'auto';
      }
    });

    const stopDrag = e => {
      if (!isDraggingWidget) return;
      isDraggingWidget = false;
      machoWidget.classList.remove('is-dragging');
      try { machoWidget.releasePointerCapture(e.pointerId); } catch {}

      if (dragHasMoved) {
        try {
          localStorage.setItem('macho_widget_pos', JSON.stringify({
            left: machoWidget.style.left,
            top: machoWidget.style.top
          }));
        } catch {}
      } else {
        // Click/tap without drag opens Copilot!
        openCopilot();
      }
    };

    machoWidget.addEventListener('pointerup', stopDrag);
    machoWidget.addEventListener('pointercancel', stopDrag);
  }

  if (floatingMachoBtn) {
    floatingMachoBtn.addEventListener('click', e => {
      if (dragHasMoved) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      openCopilot();
    });
  }

  // Keyboard shortcut: Escape only (Ctrl+K removed per user request)
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !drawer.hidden) {
      closeCopilot();
    }
  });

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

    function scrollChatToBottom() {
      if (chatBody) {
        chatBody.scrollTo({ top: chatBody.scrollHeight, behavior: 'smooth' });
      }
    }

    async function runAgentInvestigation() {
      if (_agentQueryInProgress) return;
      const question = (queryInput.value || '').trim();
      if (!question) {
        queryInput.focus();
        return;
      }

      _agentQueryInProgress = true;
      queryInput.value = '';

      const isMulti = multiToggle ? multiToggle.checked : false;
      queryBtn.disabled = true;
      queryInput.disabled = true;
      queryBtn.querySelector('.btn-text').textContent = 'Analyzing...';

      // Hide intro message once chatting starts
      if (chatIntro) chatIntro.style.display = 'none';

      // 1. Render Question as its own Chat Bubble widget on the right
      if (chatStream) {
        const userRow = document.createElement('div');
        userRow.className = 'chat-row chat-user-row';
        userRow.innerHTML = `
          <div class="chat-user-bubble">
            <div class="chat-user-label">YOU</div>
            <div class="chat-user-text">${escapeHtml(question)}</div>
          </div>
        `;
        chatStream.appendChild(userRow);

        // 2. Render Loading indicator row
        const loadingRow = document.createElement('div');
        loadingRow.className = 'chat-row chat-bot-row';
        loadingRow.id = 'chat-loading-row';
        const qLower = String(question || '').toLowerCase();
        let loadingMsg = 'Macho analyzing operational telemetry...';
        if (/\b(operator|operators|worker|staff|absent|replace|floater|crew|unstaffed)\b/.test(qLower)) {
          loadingMsg = 'Macho inspecting workforce & staffing...';
        } else if (/\b(order|orders|ord|batch|batches|due|schedule|queue)\b/.test(qLower)) {
          loadingMsg = 'Macho verifying work orders & schedules...';
        } else if (/\b(machine|motor|temperature|vibration|repair|maintenance|downtime|breakdown)\b/.test(qLower)) {
          loadingMsg = 'Macho diagnosing machine health & telemetry...';
        } else if (/\b(inventory|stock|material|supply|warehouse)\b/.test(qLower)) {
          loadingMsg = 'Macho checking inventory & materials...';
        }

        loadingRow.innerHTML = `
          <img src="/static/robot.png" alt="Macho" class="chat-avatar-mini" />
          <div class="chat-loading-bubble">
            <span class="chat-loading-dot"></span>
            <span>${escapeHtml(loadingMsg)}</span>
          </div>
        `;
        chatStream.appendChild(loadingRow);
        scrollChatToBottom();
      }

      const startTime = performance.now();
      const requestHistory = _chatHistory.slice(-8);

      try {
        const response = await fetch('/api/agent/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            question,
            factory_id: factoryId || 'F001',
            multi_agent: isMulti,
            history: requestHistory
          })
        });

        const loadingRow = byId('chat-loading-row');
        if (loadingRow) loadingRow.remove();

        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail || `Agent query failed (HTTP ${response.status})`);
        }

        const data = await response.json();
        const elapsedMs = (performance.now() - startTime) / 1000;

        // Save conversation turn to history
        _chatHistory.push({ role: 'user', content: question });
        const ans = data.final_answer || data.synthesized_answer || '';
        if (ans) {
          _chatHistory.push({ role: 'assistant', content: ans });
        }
        if (_chatHistory.length > 20) {
          _chatHistory = _chatHistory.slice(-20);
        }

        appendBotAnswer(data, elapsedMs, isMulti);
      } catch (err) {
        const loadingRow = byId('chat-loading-row');
        if (loadingRow) loadingRow.remove();

        if (chatStream) {
          const errRow = document.createElement('div');
          errRow.className = 'chat-row chat-bot-row';
          errRow.innerHTML = `
            <img src="/static/robot.png" alt="Macho" class="chat-avatar-mini" />
            <div class="chat-bot-card" style="border-left: 3px solid #ef4444;">
              <div style="color:#f87171;font-size:12.5px;"><strong>Macho Investigation Error:</strong> ${escapeHtml(err.message)}</div>
            </div>
          `;
          chatStream.appendChild(errRow);
          scrollChatToBottom();
        }
      } finally {
        _agentQueryInProgress = false;
        queryBtn.disabled = false;
        queryInput.disabled = false;
        queryBtn.querySelector('.btn-text').textContent = 'Ask Macho';
        queryInput.focus();
      }
    }

  function formatMarkdown(text) {
    if (!text) return 'No findings returned.';
    let html = escapeHtml(text);

    // Markdown tables: blocks with lines starting and ending with |
    html = html.replace(/(?:^|\n)((?:\|[^\n]+\|\r?\n?)+)/g, (match, tableBlock) => {
      const rows = tableBlock.trim().split('\n').map(r => r.trim()).filter(r => r.startsWith('|'));
      if (rows.length < 2) return match;
      let tableHtml = '<div class="copilot-table-wrapper"><table class="copilot-table">';
      let isFirst = true;
      rows.forEach(row => {
        // Skip separator row |:---|:---|
        if (/^\|[\s:\-|]+\|$/.test(row) || /^\|(?:\s*:?-+:?\s*\|)+$/.test(row)) return;
        const cells = row.split('|').slice(1, -1).map(c => c.trim());
        const tag = isFirst ? 'th' : 'td';
        tableHtml += '<tr>' + cells.map(c => `<${tag}>${c}</${tag}>`).join('') + '</tr>';
        isFirst = false;
      });
      tableHtml += '</table></div>';
      return '\n' + tableHtml + '\n';
    });

    // Bold **text**
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Strip technical evidence citations [E1], [E2], etc. and plant code tags like (F1), (`F1`)
    html = html.replace(/\s*\[E\d+\]/gi, '');
    html = html.replace(/\s*\(\s*`?F\d+`?\s*\)/gi, '');
    html = html.replace(/\s+\./g, '.');
    html = html.replace(/\s+,/g, ',');
    // Inline code `code`
    html = html.replace(/`([^`]+)`/g, '<code class="copilot-inline-code">$1</code>');
    // Headers ###
    html = html.replace(/^### (.*$)/gim, '<h4 class="copilot-h4">$1</h4>');
    // Headers ##
    html = html.replace(/^## (.*$)/gim, '<h3 class="copilot-h3">$1</h3>');
    // Headers #
    html = html.replace(/^# (.*$)/gim, '<h2 class="copilot-h2">$1</h2>');
    // Bullets - item
    html = html.replace(/^\s*[-*]\s+(.*$)/gim, '<li class="copilot-bullet">$1</li>');
    // Numbered lists 1. item
    html = html.replace(/^\s*(\d+)\.\s+(.*$)/gim, '<div class="copilot-num-item"><span class="num-badge">$1</span><span>$2</span></div>');
    // Paragraph breaks
    html = html.replace(/\n\n/g, '<br><br>');
    return html;
  }

  function appendBotAnswer(data, elapsedSec, isMulti) {
    if (!chatStream) return;

    const rawAnswer = data.final_answer || data.synthesized_answer || 'No response returned.';
    
    // Split on Reasoning & Grounding Logs or supporting table
    const splitRegex = /(?:^|\n)###\s*(?:Reasoning\s*&?\s*Grounding\s*Logs|Reasoning\s*Logs|Supporting\s*Data|Daily\s*Shift\s*Output\s*Comparison)/i;
    const match = rawAnswer.match(splitRegex);

    let directAnswerRaw = rawAnswer;
    let reasoningRaw = '';

    if (match) {
      directAnswerRaw = rawAnswer.substring(0, match.index).trim();
      reasoningRaw = rawAnswer.substring(match.index + match[0].length).trim();
    }

    // Determine verdict type from direct answer
    let verdictBadge = '';
    let containerClass = 'verdict-info-card';
    const daUpper = directAnswerRaw.toUpperCase();

    if (daUpper.includes('YES (STOCK IS SUFFICIENT') || daUpper.includes('### DIRECT ANSWER: YES') || daUpper.includes('**YES**')) {
      verdictBadge = '<span class="verdict-tag verdict-yes"><span class="verdict-dot"></span>YES · SUFFICIENT</span>';
      containerClass = 'verdict-yes-card';
    } else if (daUpper.includes('NO (STOCK DEFICIT') || daUpper.includes('### DIRECT ANSWER: NO') || daUpper.includes('**NO**')) {
      verdictBadge = '<span class="verdict-tag verdict-no"><span class="verdict-dot"></span>NO · DEFICIT DETECTED</span>';
      containerClass = 'verdict-no-card';
    } else if (daUpper.includes('PARTIALLY SUFFICIENT') || daUpper.includes('REORDER ATTENTION') || daUpper.includes('REORDER REQUIRED') || daUpper.includes('MATERIAL SHORTAGE')) {
      verdictBadge = '<span class="verdict-tag verdict-warn"><span class="verdict-dot"></span>ATTENTION · REORDER REQUIRED</span>';
      containerClass = 'verdict-warn-card';
    } else if (daUpper.includes('PLANT ISOLATION')) {
      verdictBadge = '<span class="verdict-tag verdict-isolation"><span class="verdict-dot"></span>PLANT ISOLATION BOUNDARY</span>';
      containerClass = 'verdict-isolation-card';
    } else if (daUpper.includes('MACHO ASSISTANT') || daUpper.includes('CAPABILITIES OVERVIEW')) {
      verdictBadge = '<span class="verdict-tag verdict-info"><span class="verdict-dot"></span>MACHO AI COPILOT</span>';
      containerClass = 'verdict-info-card';
    } else {
      verdictBadge = '<span class="verdict-tag verdict-info"><span class="verdict-dot"></span>DIRECT ANSWER</span>';
    }

    // Clean direct answer header text if present
    let cleanDirect = directAnswerRaw.replace(/^###\s*Direct\s*Answer:[^\n]*\n+/i, '').replace(/^###\s*[^\n]+\n+/i, '');
    let directHtml = formatMarkdown(cleanDirect);

    const uid = 'r' + Math.random().toString(36).substring(2, 8);

    let reasoningWidgetHtml = '';
    if (reasoningRaw) {
      let reasoningHtml = formatMarkdown(reasoningRaw);
      reasoningWidgetHtml = `
        <div class="reasoning-logs-widget" id="reasoning-logs-widget-${uid}">
          <div class="reasoning-widget-header">
            <div class="reasoning-header-left">
              <span class="reasoning-icon">📋</span>
              <span class="reasoning-title">Reasoning & Telemetry Logs</span>
              <span class="reasoning-count-pill" id="reasoning-count-pill-${uid}">Records</span>
            </div>
            <div class="reasoning-search-wrap">
              <span class="reasoning-search-icon">🔍</span>
              <input type="text" id="reasoning-search-input-${uid}" class="reasoning-search-input" placeholder="Search logs, materials, equipment..." autocomplete="off" />
              <button type="button" id="reasoning-search-clear-${uid}" class="reasoning-search-clear" title="Clear filter" style="display:none;">✕</button>
            </div>
          </div>
          <div class="reasoning-widget-body" id="reasoning-widget-body-${uid}">
            ${reasoningHtml}
          </div>
          <div class="reasoning-empty-search" id="reasoning-empty-search-${uid}" style="display:none;">
            No records matching "<span id="reasoning-empty-query-${uid}"></span>"
          </div>
        </div>
      `;
    }

    const botRow = document.createElement('div');
    botRow.className = 'chat-row chat-bot-row';
    botRow.innerHTML = `
      <img src="/static/robot.png" alt="Macho" class="chat-avatar-mini" />
      <div class="chat-bot-card">
        <div class="chat-bot-header">
          <div class="chat-bot-name">Macho</div>
          <div class="chat-bot-meta">${elapsedSec.toFixed(2)}s · ${isMulti ? 'Multi-Agent' : 'Single-Agent'}</div>
        </div>

        <div class="direct-answer-container ${containerClass}">
          <div class="direct-answer-header">
            ${verdictBadge}
            <span class="direct-answer-subtitle">EXECUTIVE VERDICT</span>
          </div>
          <div class="direct-answer-content">
            ${directHtml}
          </div>
        </div>

        ${reasoningWidgetHtml}
      </div>
    `;

    chatStream.appendChild(botRow);

    if (reasoningRaw) {
      initReasoningSearch(uid);
    }

    if (chatBody) {
      chatBody.scrollTo({ top: chatBody.scrollHeight, behavior: 'smooth' });
    }
  }

  function initReasoningSearch(uid) {
    const input = byId(`reasoning-search-input-${uid}`);
    const clearBtn = byId(`reasoning-search-clear-${uid}`);
    const body = byId(`reasoning-widget-body-${uid}`);
    const countPill = byId(`reasoning-count-pill-${uid}`);
    const emptyBox = byId(`reasoning-empty-search-${uid}`);
    const emptyQuery = byId(`reasoning-empty-query-${uid}`);

    if (!input || !body) return;

    const rows = Array.from(body.querySelectorAll('table.copilot-table tr:not(:first-child), li.copilot-bullet'));
    const totalCount = rows.length;
    if (countPill) {
      countPill.textContent = totalCount > 0 ? `${totalCount} records` : 'Logs';
    }

    function filterRows() {
      const q = (input.value || '').trim().toLowerCase();
      if (clearBtn) {
        clearBtn.style.display = q ? 'inline-block' : 'none';
      }

      if (!q) {
        rows.forEach(r => r.style.display = '');
        if (countPill) countPill.textContent = totalCount > 0 ? `${totalCount} records` : 'Logs';
        if (emptyBox) emptyBox.style.display = 'none';
        return;
      }

      let matchCount = 0;
      rows.forEach(r => {
        const text = r.innerText.toLowerCase();
        if (text.includes(q)) {
          r.style.display = '';
          matchCount++;
        } else {
          r.style.display = 'none';
        }
      });

      if (countPill) {
        countPill.textContent = `${matchCount} of ${totalCount}`;
      }
      if (emptyBox) {
        if (matchCount === 0) {
          if (emptyQuery) emptyQuery.textContent = input.value;
          emptyBox.style.display = 'block';
        } else {
          emptyBox.style.display = 'none';
        }
      }
    }

    input.addEventListener('input', filterRows);
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        input.value = '';
        filterRows();
        input.focus();
      });
    }
  }
}

// Initialize Copilot on load
document.addEventListener('DOMContentLoaded', initCopilot);
if (document.readyState !== 'loading') initCopilot();

/* -------------------------------------------------------------
   Workforce Staffing & Free Pool Floater Assignment Handlers
   ------------------------------------------------------------- */
window.handleOpenAssignModal = function(machineId) {
  if (!employeesData || !employeesData.machine_coverage) return;
  const cov = employeesData.machine_coverage[machineId];
  if (!cov) return;

  const modal = byId('floater-modal');
  const backdrop = byId('floater-modal-backdrop');
  const body = byId('floater-modal-body');
  if (!modal || !backdrop || !body) return;

  const freePool = employeesData.free_pool || [];
  const reqSkill = cov.required_skill || '';
  const matchingFloaters = cov.eligible_free_pool || [];

  let floatersHtml = '';
  if (matchingFloaters.length > 0) {
    floatersHtml = `
      <div class="floater-list">
        ${matchingFloaters.map(f => `
          <div class="floater-card">
            <div class="floater-info">
              <span class="floater-name">${escapeHtml(f.name)} <span class="floater-id-role">(${escapeHtml(f.employee_id)} · ${escapeHtml(f.role)})</span></span>
              <div class="floater-skills-wrap">
                ${(f.skills || []).map(s => {
                  const isMatch = s.toLowerCase().includes(reqSkill.toLowerCase());
                  return `<span class="floater-skill-pill ${isMatch ? 'is-match' : ''}">${escapeHtml(s)}</span>`;
                }).join('')}
              </div>
            </div>
            <button type="button" class="btn-confirm-assign" onclick="window.executeAssignOperator('${escapeHtml(machineId)}', '${escapeHtml(f.employee_id)}')">
              Assign to Station
            </button>
          </div>
        `).join('')}
      </div>
    `;
  } else {
    floatersHtml = `
      <div class="no-floaters-box">
        <strong>No Standby Floaters Certified in "${escapeHtml(reqSkill)}"</strong><br>
        Currently none of the standby floaters in the Free Pool have completed certification for <b>${escapeHtml(reqSkill)}</b>.<br>
        Only qualified floaters possessing this machine certification may be assigned.
      </div>
    `;
  }

  body.innerHTML = `
    <div class="modal-station-banner">
      <div class="modal-station-title">Workstation: ${escapeHtml(cov.workstation_name || machineId)} · ${escapeHtml(machineId)}</div>
      <div class="modal-station-meta">
        <span>Line: <strong>${escapeHtml(cov.line_id)}</strong></span>
        <span>Required Skill: <strong class="skill-tag">${escapeHtml(reqSkill)}</strong></span>
        <span>Primary Operator: <strong>${escapeHtml(cov.primary_operator?.name || 'Assigned')}</strong> (${escapeHtml(cov.primary_operator?.attendance_note || 'Absent')})</span>
      </div>
    </div>
    <div class="modal-section-title">Available Free Pool Standby Floaters (${matchingFloaters.length} qualified)</div>
    ${floatersHtml}
  `;

  modal.hidden = false;
  backdrop.hidden = false;
};

window.executeAssignOperator = async function(machineId, employeeId) {
  try {
    const res = await fetch(`/api/factories/${encodeURIComponent(factoryId)}/assign-operator`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ machine_id: machineId, employee_id: employeeId })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to assign operator.');

    window.closeFloaterModal();

    const toast = byId('toast');
    toast.textContent = data.message || `Assigned floater ${employeeId} to ${machineId}. Station is operational.`;
    toast.hidden = false;
    setTimeout(() => { toast.hidden = true; }, 4000);

    await loadDashboard();
  } catch (err) {
    alert(err.message);
  }
};

function showConfirmDialog(title, message, okText = 'Confirm') {
  return new Promise(resolve => {
    const modal = byId('confirm-modal');
    const backdrop = byId('confirm-modal-backdrop');
    const titleEl = byId('confirm-modal-title');
    const msgEl = byId('confirm-modal-message');
    const okBtn = byId('confirm-ok-btn');
    const cancelBtn = byId('confirm-cancel-btn');
    const closeBtn = byId('close-confirm-modal-btn');
    if (!modal || !backdrop) {
      resolve(window.confirm(message));
      return;
    }
    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;
    if (okBtn) okBtn.textContent = okText;
    modal.hidden = false;
    backdrop.hidden = false;

    function cleanup(result) {
      modal.hidden = true;
      backdrop.hidden = true;
      if (okBtn) okBtn.removeEventListener('click', onOk);
      if (cancelBtn) cancelBtn.removeEventListener('click', onCancel);
      if (closeBtn) closeBtn.removeEventListener('click', onCancel);
      if (backdrop) backdrop.removeEventListener('click', onCancel);
      resolve(result);
    }
    function onOk() { cleanup(true); }
    function onCancel() { cleanup(false); }

    if (okBtn) okBtn.addEventListener('click', onOk, { once: true });
    if (cancelBtn) cancelBtn.addEventListener('click', onCancel, { once: true });
    if (closeBtn) closeBtn.addEventListener('click', onCancel, { once: true });
    if (backdrop) backdrop.addEventListener('click', onCancel, { once: true });
  });
}

window.handleUnassignOperator = async function(machineId) {
  const confirmed = await showConfirmDialog(
    'Return Floater to Free Pool',
    `Return relief floater to Free Pool? Station ${machineId} will become unstaffed.`,
    'Release to Free Pool'
  );
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/factories/${encodeURIComponent(factoryId)}/unassign-operator`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ machine_id: machineId })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to unassign operator.');

    const toast = byId('toast');
    if (toast) {
      toast.textContent = data.message || `Floater returned to Free Pool. Station ${machineId} is unstaffed.`;
      toast.hidden = false;
      setTimeout(() => { toast.hidden = true; }, 4000);
    }

    await loadDashboard();
  } catch (err) {
    const toast = byId('toast');
    if (toast) {
      toast.textContent = err.message;
      toast.hidden = false;
      setTimeout(() => { toast.hidden = true; }, 4000);
    } else {
      alert(err.message);
    }
  }
};

window.closeFloaterModal = function() {
  const modal = byId('floater-modal');
  const backdrop = byId('floater-modal-backdrop');
  if (modal) modal.hidden = true;
  if (backdrop) backdrop.hidden = true;
};

window.handleOpenRosterModal = function() {
  if (!employeesData) return;
  const modal = byId('roster-modal');
  const backdrop = byId('roster-modal-backdrop');
  const body = byId('roster-modal-body');
  if (!modal || !backdrop || !body) return;

  const sum = employeesData.summary || {};
  const coverage = employeesData.machine_coverage || {};
  const absentList = employeesData.absent || [];
  const freePool = employeesData.free_pool || [];

  body.innerHTML = `
    <div class="roster-grid-kpis">
      <div class="roster-kpi-card">
        <span>SHIFT WORKFORCE</span>
        <strong>${sum.total_roster || 0}</strong>
      </div>
      <div class="roster-kpi-card">
        <span>ATTENDANCE RATE</span>
        <strong>${sum.attendance_pct || '0%'}</strong>
      </div>
      <div class="roster-kpi-card">
        <span>FREE POOL (STANDBY)</span>
        <strong>${sum.free_pool_count ?? 0}</strong>
      </div>
      <div class="roster-kpi-card">
        <span>UNSTAFFED STATIONS</span>
        <strong style="color: #dc2626;">${sum.unstaffed_count ?? 0}</strong>
      </div>
    </div>

    <div class="modal-section-title">Workstation Absenteeism & Relief Coverage</div>
    <table class="roster-table">
      <thead>
        <tr>
          <th>Station / Machine</th>
          <th>Primary Operator</th>
          <th>Required Skill</th>
          <th>Attendance / Note</th>
          <th>Relief Status</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${absentList.map(emp => {
          const mId = emp.machine_id;
          const cov = coverage[mId] || {};
          const isCovered = cov.is_replacement && cov.active_operator;
          return `
            <tr>
              <td><strong>${escapeHtml(mId)}</strong> (${escapeHtml(cov.workstation_name || 'Station')})</td>
              <td>${escapeHtml(emp.name)} <span class="roster-status-pill is-absent">${escapeHtml(emp.employee_id)}</span></td>
              <td><span class="skill-tag">${escapeHtml(cov.required_skill || emp.primary_skill || '')}</span></td>
              <td><span class="roster-status-pill is-absent">${escapeHtml(emp.attendance_note || 'Absent')}</span></td>
              <td>
                ${isCovered 
                  ? `<span class="roster-status-pill is-active">Covered by ${escapeHtml(cov.active_operator.name)}</span>` 
                  : `<span class="roster-status-pill is-absent">Unstaffed Bottleneck</span>`}
              </td>
              <td>
                ${isCovered 
                  ? `<button type="button" class="btn-unassign-floater" onclick="window.closeRosterModal(); window.handleUnassignOperator('${escapeHtml(mId)}')">Release</button>` 
                  : `<button type="button" class="btn-assign-floater" onclick="window.closeRosterModal(); window.handleOpenAssignModal('${escapeHtml(mId)}')">Assign Floater ▾</button>`}
              </td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>

    <div class="modal-section-title" style="margin-top:24px;">Standby Free Pool Floaters (Cross-Skill Matrix)</div>
    <table class="roster-table">
      <thead>
        <tr>
          <th>Employee ID</th>
          <th>Floater Name</th>
          <th>Role</th>
          <th>Certified Skill Matrix</th>
          <th>Clock-In</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        ${freePool.map(f => `
          <tr>
            <td><strong>${escapeHtml(f.employee_id)}</strong></td>
            <td>${escapeHtml(f.name)}</td>
            <td>${escapeHtml(f.role)}</td>
            <td>
              <div class="floater-skills-wrap">
                ${(f.skills || []).map(s => `<span class="floater-skill-pill">${escapeHtml(s)}</span>`).join('')}
              </div>
            </td>
            <td>${escapeHtml(f.clock_in || '06:00')}</td>
            <td><span class="roster-status-pill is-freepool">Standby Free Pool</span></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;

  modal.hidden = false;
  backdrop.hidden = false;
};

window.closeRosterModal = function() {
  const modal = byId('roster-modal');
  const backdrop = byId('roster-modal-backdrop');
  if (modal) modal.hidden = true;
  if (backdrop) backdrop.hidden = true;
};

// Wire modal event listeners
document.addEventListener('DOMContentLoaded', () => {
  byId('close-floater-modal-btn')?.addEventListener('click', window.closeFloaterModal);
  byId('floater-modal-backdrop')?.addEventListener('click', window.closeFloaterModal);
  byId('close-roster-modal-btn')?.addEventListener('click', window.closeRosterModal);
  byId('roster-modal-backdrop')?.addEventListener('click', window.closeRosterModal);
  byId('open-roster-btn')?.addEventListener('click', window.handleOpenRosterModal);
  
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      window.closeFloaterModal();
      window.closeRosterModal();
    }
  });
});
if (document.readyState !== 'loading') {
  byId('close-floater-modal-btn')?.addEventListener('click', window.closeFloaterModal);
  byId('floater-modal-backdrop')?.addEventListener('click', window.closeFloaterModal);
  byId('close-roster-modal-btn')?.addEventListener('click', window.closeRosterModal);
  byId('roster-modal-backdrop')?.addEventListener('click', window.closeRosterModal);
  byId('open-roster-btn')?.addEventListener('click', window.handleOpenRosterModal);
}

window.handleDispatchNextOrder = async function(lineId, nextOrderId) {
  try {
    const res = await fetch(`/api/factories/${encodeURIComponent(factoryId)}/orders/dispatch-next`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ line_id: lineId, order_id: nextOrderId }),
    });
    const data = await res.json();
    if (data.success) {
      const toastEl = byId('toast');
      if (toastEl) {
        toastEl.textContent = data.message;
        toastEl.hidden = false;
        setTimeout(() => { toastEl.hidden = true; }, 4000);
      }
      await loadDashboard();
    } else {
      alert(data.message || 'Could not dispatch next order.');
    }
  } catch (err) {
    console.error('Dispatch next order error:', err);
    alert('Failed to dispatch order: ' + err.message);
  }
};

