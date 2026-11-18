import { drills } from '../stores/sync.js';
import * as syncApi from '../api/sync.js';
import { header } from '../components/common/header.js';
import { table } from '../components/common/table.js';
import { esc } from '../utils/html.js';
import { submit, notify } from '../hooks/feedback.js';
import { session } from '../stores/session.js';
import { SYNC_POLICIES, SYNC_EVENT, CONFLICT_KIND } from '../constants/enums.js';

const writable = () => session.get().role !== 'viewer';

function siteCard(sid, d) {
  const s = d.sites[sid];
  const rows = Object.entries(s.kv);
  return `<article class="panel subtle site-card">
    <div class="actions" style="margin-top:0">
      <h2 style="margin:0">站点 ${sid}</h2>
      <span class="badge ${s.online ? 'passed' : 'failed'}">${s.online ? '链路在线' : '已断链'}</span>
      <span class="badge mono">Lamport ${s.clock}</span>
      <span class="badge mono">${s.op_count} ops</span>
    </div>
    <p class="helper" style="margin:10px 0">存活键 <strong>${rows.length}</strong> · 状态校验和 <span class="mono">${esc(s.checksum)}</span>${s.tombstones.length ? `<br>墓碑（仍保留，阻止旧值复活）：${s.tombstones.map(k => `<span class="badge">${esc(k)}</span>`).join(' ')}` : ''}</p>
    ${rows.length ? table(['键', '本地值'], rows.map(([k, v]) => [esc(k), '<span class="mono">' + esc(v) + '</span>'])) : '<div class="empty">本站暂无存活键。断链时的写入只保存在本站。</div>'}
    ${writable() ? `<form class="site-write" data-site="${sid}">
      <label>在站点 ${sid} 写入（断链也可用）</label>
      <div class="formrow">
        <select name="op"><option value="put">put 写入</option><option value="delete">delete 墓碑</option></select>
        <input name="key" required maxlength="64" placeholder="键，如 sensor-02">
        <input name="value" maxlength="200" placeholder="值（delete 时忽略）">
        <button class="primary" type="submit">写入 →</button>
      </div>
    </form>` : '<p class="helper">观察员只读：可查看两站分歧与同步收敛过程。</p>'}
  </article>`;
}

function diffTable(d) {
  const keys = [...new Set([...Object.keys(d.sites.A.kv), ...Object.keys(d.sites.B.kv)])].sort();
  const cell = (kv, k) => (k in kv ? '<span class="mono">' + esc(kv[k]) + '</span>' : '<span class="muted">∅ 不存在/已删除</span>');
  if (!keys.length) return '<div class="empty">两站都还没有键。先断链，再分别写入制造分歧。</div>';
  return '<div class="tablewrap"><table><thead><tr><th>键</th><th>站点 A</th><th>站点 B</th><th>裁决状态</th></tr></thead><tbody>' +
    keys.map(k => {
      const same = d.sites.A.kv[k] === d.sites.B.kv[k] && (k in d.sites.A.kv) === (k in d.sites.B.kv);
      return `<tr class="${same ? '' : 'different'}"><td>${esc(k)}</td><td>${cell(d.sites.A.kv, k)}</td><td>${cell(d.sites.B.kv, k)}</td><td>${same ? '<span class="badge passed">一致</span>' : '<span class="badge different">分歧</span>'}</td></tr>`;
    }).join('') + '</tbody></table></div>';
}

function conflictTable(d) {
  if (!d.conflicts.length) return '<div class="empty">尚无跨站冲突记录。重连同键并发写入后，这里展示每一次取舍。</div>';
  return table(['键', '冲突类型', '观察站点', '时钟(新/旧)', '策略', '取舍'], d.conflicts.map(c => [
    esc(c.key),
    (c.concurrent ? '并发 · ' : 'LWW 排序 · ') + esc(CONFLICT_KIND[c.kind] || c.kind),
    esc(c.site),
    c.clock + ' / ' + c.losing_clock,
    esc(SYNC_POLICIES[c.policy] || c.policy),
    c.resolution === 'accepted' ? `接受 <span class="mono">${esc(c.kept)}</span>，丢弃 <span class="mono">${esc(c.dropped)}</span>` : `保留 <span class="mono">${esc(c.kept)}</span>，拒绝 <span class="mono">${esc(c.dropped)}</span>`
  ]));
}

function eventTable(d) {
  if (!d.events.length) return '<div class="empty">暂无操作轨迹。</div>';
  return table(['#', '事件', '站点', '说明'], d.events.slice(0, 40).map(e => {
    const detail = e.event === 'sync_batch' ? `交换 ${e.exchanged} 条，剩余 ${e.remaining}，${e.completed ? '已收敛' : '未完成'}`
      : e.event === 'sync_blocked' ? '链路断开，同步被阻止'
      : e.event === 'link' ? (e.online ? '恢复连接' : '断开连接')
      : e.event === 'local_write' ? `${e.deleted ? 'delete' : 'put'} ${e.key}（${e.online ? '在线' : '离线'}）`
      : `${e.via === 'local' ? '本地' : '来自站点 ' + esc(e.remote_site || '')} · ${e.deleted ? 'delete' : 'put'} ${esc(e.key)} · ${e.changed ? '改变获胜值' : '旧操作，未改变'}`;
    return [e.seq, esc(SYNC_EVENT[e.event] || e.event), esc(e.site || 'A/B'), detail];
  }));
}

async function drain(id, limit) {
  // Worst case: 200 ops on one site with batch size 1 -> up to 200 one-way rounds.
  for (let i = 0; i < 250; i++) {
    const r = await syncApi.runSync(id, limit);
    if (r.report.blocked) { notify('链路处于断开状态，同步已停止；恢复连接后可继续，已应用的操作不会重复。'); return false; }
    if (r.report.completed) return true;
  }
  notify('达到最大同步轮次仍有积压，请继续点击同步。');
  return false;
}

async function detailView(el, id) {
  const d = await drills.open(id);
  const v = d.invariants;
  el.innerHTML = header('05', '两站离线各自读写，重连后确定性收敛', '每条写入带 Lamport 时钟并保存在本站操作日志；高时钟永远胜，墓碑不被迟到的旧 put 复活。同步按批交换对方缺失的操作，中断后重试幂等，不会重复应用。') + `
  <div class="actions" style="margin-bottom:20px">
    <a href="#sync" class="backlink">← 返回演练列表</a>
    <span class="material-tag">${esc(d.name)}</span>
    <span class="badge">${esc(SYNC_POLICIES[d.policy])}</span>
    <span class="badge ${v.converged ? 'passed' : 'different'}">${v.converged ? '两站已一致' : `积压 ${d.backlog} 条`}</span>
    ${writable() ? '<button id="reset" type="button">重置演练</button>' : ''}
  </div>
  <section class="panel">
    <h2>链路控制</h2>
    <p class="modelnote">断链后两站仍可各自读写本地键值；重连后用分批同步补齐。同步到一半再次断连时，已交换的操作保留在对方日志中，恢复后只补发缺失部分。</p>
    <div class="actions">
      ${writable() ? `<button id="link-down" class="${d.online ? '' : 'primary'}" type="button" ${!d.online ? 'disabled' : ''}>断开两站连接</button>
      <button id="link-up" class="${d.online ? 'primary' : ''}" type="button" ${d.online ? 'disabled' : ''}>恢复连接</button>` : `<span class="badge ${d.online ? 'passed' : 'failed'}">${d.online ? '当前在线' : '当前断链'}</span>`}
      <label class="inline-label">每批交换上限 <input id="batch-limit" type="number" min="1" max="200" value="3" style="width:90px;display:inline-block"></label>
      ${writable() ? '<button id="sync-once" class="primary" type="button">执行一轮分批同步</button><button id="sync-drain" type="button">同步至收敛</button>' : ''}
    </div>
    <div id="last-report" class="helper"></div>
  </section>
  ${writable() ? `<section class="panel">
    <h2>演练剧本（一键编排，仍可在下方手动操作）</h2>
    <div class="actions">
      <button class="scenario" data-scenario="contest" type="button">剧本一：断链 → A 删键 / B 同时覆盖</button>
      <button class="scenario" data-scenario="halfsync" type="button">剧本二：断链写入 → 同步到一半再次断连</button>
    </div>
    <p class="helper">剧本一制造同钟并发的墓碑与覆盖，重连同步后在“冲突取舍”中查看裁决；剧本二先积压 6 个键，仅同步一批就再次断链，可验证恢复后不重复应用。</p>
  </section>` : ''}
  <div class="duo">${siteCard('A', d)}${siteCard('B', d)}</div>
  <section class="panel"><h2>两站键值：从分歧到一致</h2>${diffTable(d)}</section>
  <section class="panel">
    <h2>冲突取舍记录</h2>
    <p class="helper">并发（相同时钟）按演练策略裁决；时钟不同则按 Lamport 顺序——这正是“迟到的旧写入不能让删键复活”的保证。两站以相同规则独立得到相同结果。</p>
    ${conflictTable(d)}
  </section>
  <div class="split">
    <section class="panel"><h2>不变量核查</h2>
      <div class="metrics">
        <div><small>待同步操作（积压）</small><strong>${v.backlog}</strong></div>
        <div><small>两站校验和 A / B</small><strong style="font-size:15px">${esc(v.checksum_a)}<br>${esc(v.checksum_b)}</strong></div>
        <div><small>收敛状态</small><strong>${v.converged ? '一致' : '分歧'}</strong></div>
      </div>
      ${v.problems.length ? '<p class="error">' + v.problems.map(esc).join('；') + '</p>' : '<p class="helper">操作日志可重建键值、无重复应用、无旧值复活。</p>'}
    </section>
    <section class="panel"><h2>操作轨迹（最近40条）</h2>${eventTable(d)}</section>
  </div>`;

  const refresh = () => detailView(el, id);
  const guard = (btn, fn) => submit(btn, fn);
  if (writable()) {
    el.querySelector('#reset').onclick = e => guard(e.target, async () => { await syncApi.reset(id); notify('演练已重置为初始两站状态'); await refresh(); });
    el.querySelector('#link-down').onclick = e => guard(e.target, async () => { await syncApi.setLink(id, false); notify('两站连接已断开，各自写入只保存在本地'); await refresh(); });
    el.querySelector('#link-up').onclick = e => guard(e.target, async () => { await syncApi.setLink(id, true); notify('连接已恢复，可以分批同步'); await refresh(); });
    el.querySelector('#sync-once').onclick = e => guard(e.target, async () => {
      const limit = Number(el.querySelector('#batch-limit').value) || 3;
      const r = await syncApi.runSync(id, limit);
      notify(r.report.blocked ? '链路断开，本轮同步未执行' : `本轮交换 ${r.report.exchanged} 条，剩余积压 ${r.report.remaining} 条${r.report.completed ? '，两站已收敛' : ''}`);
      await refresh();
    });
    el.querySelector('#sync-drain').onclick = e => guard(e.target, async () => {
      const limit = Number(el.querySelector('#batch-limit').value) || 3;
      const ok = await drain(id, limit);
      if (ok) notify('同步完成，两站键值已收敛一致');
      await refresh();
    });
    el.querySelectorAll('form.site-write').forEach(form => {
      form.onsubmit = e => {
        e.preventDefault();
        submit(form, async () => {
          const f = Object.fromEntries(new FormData(form));
          await syncApi.writeOp(id, { site: form.dataset.site, op: f.op, key: f.key, value: f.value || null });
          notify(`站点 ${form.dataset.site} 已${f.op === 'delete' ? '写入墓碑' : '写入键值'}`);
          await refresh();
        });
      };
    });
    el.querySelectorAll('button.scenario').forEach(btn => {
      btn.onclick = () => guard(btn, async () => {
        if (btn.dataset.scenario === 'contest') {
          await syncApi.setLink(id, false);
          await syncApi.writeOp(id, { site: 'A', op: 'delete', key: 'scenario-key', value: null });
          await syncApi.writeOp(id, { site: 'B', op: 'put', key: 'scenario-key', value: '并发覆盖值' });
          notify('断链对峙已制造：A 删除 scenario-key，B 同时覆盖。恢复连接并执行同步查看取舍。');
        } else {
          await syncApi.setLink(id, false);
          for (let i = 1; i <= 6; i++) await syncApi.writeOp(id, { site: 'A', op: 'put', key: `half-${i}`, value: `v${i}` });
          await syncApi.setLink(id, true);
          await syncApi.runSync(id, 2);
          await syncApi.setLink(id, false);
          notify('仅同步一批（2条）后再次断连；恢复连接继续同步，可验证旧操作不会重复应用。');
        }
        await refresh();
      });
    });
  }
}

export async function syncPage(el) {
  const id = Number(location.hash.split('/')[1]);
  if (id) return detailView(el, id);
  const rows = await drills.load();
  el.innerHTML = header('05', '双站断网同步演练', '两个逻辑站点在断链期间独立读写，重连后基于 Lamport 时钟与墓碑做确定性收敛；支持断链/恢复、分批同步、中断重试与冲突取舍复盘。') + `
  <p class="modelnote">教学模型：同步状态完整保存在 MySQL 的 JSON 字段中（每站操作日志、时钟、键值、事件与冲突裁决），不模拟真实网络栈或分布式共识。</p>
  <div class="split">
    <section class="panel">
      <span class="material-tag">SYNC DRILLS / ${rows.length}</span>
      ${rows.map(x => `<article class="record">
        <h3>${esc(x.name)}</h3>
        <p class="muted">${esc(x.description)}</p>
        <div class="actions" style="margin:8px 0">
          <span class="badge ${x.online ? 'passed' : 'failed'}">${x.online ? '链路在线' : '链路断开'}</span>
          <span class="badge ${x.converged ? 'passed' : 'different'}">${x.converged ? '两站一致' : `积压 ${x.backlog}`}</span>
          <span class="badge">A ${x.sites.A.op_count} ops / B ${x.sites.B.op_count} ops</span>
          <span class="badge">${x.conflicts.length} 次冲突裁决</span>
        </div>
        <div class="actions"><a href="#sync/${x.id}">进入演练 →</a></div>
      </article>`).join('') || '<div class="empty">还没有演练，在右侧新建一个。</div>'}
    </section>
    <aside class="panel">
      <h2>${writable() ? '新建同步演练' : '演练说明'}</h2>
      ${writable() ? `<form id="drill-form">
        <label>演练名称</label><input name="name" required maxlength="120" placeholder="dual-site-20261118">
        <label>业务说明</label><input name="description" maxlength="2000" placeholder="断网窗口、对峙键与预期收敛结果">
        <label>冲突策略</label>
        <select name="policy">${Object.entries(SYNC_POLICIES).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select>
        <div class="actions"><button class="primary" type="submit">创建演练 →</button></div>
      </form>` : ''}
      <p class="helper">Lamport 时钟较大的操作永远获胜，因此晚到的旧 put 无法覆盖新墓碑；时钟相同才是真并发，按所选策略与站点号裁决，两站得到同一结果。单演练最多保留 200 条操作，可随时重置。</p>
    </aside>
  </div>`;
  if (writable()) el.querySelector('form').onsubmit = e => {
    e.preventDefault();
    submit(e.target, async () => {
      const d = await syncApi.create(Object.fromEntries(new FormData(e.target)));
      notify('同步演练已创建');
      location.hash = 'sync/' + d.id;
    });
  };
}
