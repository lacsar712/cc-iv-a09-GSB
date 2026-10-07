<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <button :class="{ active: page === 'scan' }" @click="page = 'scan'">IV 扫描录入</button>
        <button :class="{ active: page === 'roster' }" @click="page = 'roster'">抽检名册</button>
        <span class="spacer"></span>
        <span class="who">{{ session.username }}（{{ isWriter ? "可提交" : "观察员只读" }}）</span>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <!-- ============ IV 扫描录入 ============ -->
      <div v-if="page === 'scan'">
        <section v-if="isWriter">
          <label>到货批次（必选，仅已抽检批次可入队）</label>
          <select v-model="batchId">
            <option value="">请选择已抽检批次</option>
            <option v-for="b in roster.batches" :key="b.id"
                    :value="b.id" :disabled="b.status !== 'inspected'">
              {{ b.batch_no }}{{ b.status === "pending" ? "（待抽检，不可入队）" : b.status === "void" ? "（已作废）" : "（已抽检）" }}
            </option>
          </select>
          <p v-if="selectedBatch" class="hint">
            批次 {{ selectedBatch.batch_no }} 已绑定组串：
            <template v-if="bindingsOf(selectedBatch.id).length">
              <span v-for="s in bindingsOf(selectedBatch.id)" :key="s" class="chip">{{ s }}</span>
            </template>
            <span v-else>暂无，未绑定的组串提交会整笔退回</span>
          </p>
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <div class="rowhead">
            <h2>扫描记录</h2>
            <button class="secondary" @click="refresh">刷新列表</button>
          </div>
          <table>
            <thead>
              <tr><th>编号</th><th>批次</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.batch_no || "—" }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 抽检名册 ============ -->
      <div v-if="page === 'roster'">
        <p class="sub">到货批次名册：左列为待抽检批次，中列为已抽检（含已作废）批次，右列为批次绑定组串样例。{{ isWriter ? "" : "观察员可翻阅名册，但任何抽检、作废、绑定操作均被服务端拒绝。" }}</p>
        <p v-if="rosterError" class="err">{{ rosterError }}</p>
        <div class="cols">
          <!-- 左列：待抽检 -->
          <section class="col">
            <h2>待抽检</h2>
            <div v-if="isWriter" class="mini">
              <input v-model="newBatchNo" placeholder="新到货批次号" @keyup.enter="createBatch" />
              <button :disabled="loading" @click="createBatch">登记批次</button>
            </div>
            <p v-if="pendingBatches.length === 0" class="empty">暂无待抽检批次</p>
            <div v-for="b in pendingBatches" :key="b.id" class="card">
              <div class="card-title">{{ b.batch_no }}</div>
              <div class="muted">登记：{{ fmt(b.created_at) }}</div>
              <button v-if="isWriter" :disabled="loading" @click="inspect(b.id)">录入抽检</button>
            </div>
          </section>

          <!-- 中列：已抽检 -->
          <section class="col">
            <h2>已抽检</h2>
            <p v-if="inspectedBatches.length === 0" class="empty">暂无已抽检批次</p>
            <div v-for="b in inspectedBatches" :key="b.id" class="card" :class="{ voided: b.status === 'void' }">
              <div class="card-title">
                {{ b.batch_no }}
                <span v-if="b.status === 'void'" class="tag bad">已作废</span>
                <span v-else class="tag ok">已抽检</span>
              </div>
              <div class="muted">抽检：{{ fmt(b.inspected_at) }} · {{ b.inspected_by }}</div>
              <div v-if="b.status === 'void'" class="muted">作废：{{ fmt(b.voided_at) }} · {{ b.voided_by }}</div>
              <template v-if="isWriter && b.status === 'inspected'">
                <div class="mini">
                  <input v-model="bindInput[b.id]" placeholder="绑定组串编号，如 阵列A-串03" @keyup.enter="bind(b.id)" />
                  <button :disabled="loading" @click="bind(b.id)">绑定组串</button>
                </div>
                <button class="secondary" :disabled="loading" @click="voidBatch(b.id)">作废批次</button>
              </template>
            </div>
          </section>

          <!-- 右列：绑定样例 -->
          <section class="col">
            <h2>绑定样例</h2>
            <p v-if="roster.bindings.length === 0" class="empty">还没有抽检记录</p>
            <table v-else>
              <thead><tr><th>批次</th><th>组串</th></tr></thead>
              <tbody>
                <tr v-for="g in bindingSamples" :key="g.key">
                  <td>{{ g.batch_no }}</td>
                  <td>{{ g.string_code }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const page = ref("scan");
const logs = ref([]);
const roster = reactive({ batches: [], bindings: [] });
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const batchId = ref("");
const newBatchNo = ref("");
const bindInput = reactive({});
const error = ref("");
const rosterError = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const pendingBatches = computed(() => roster.batches.filter((b) => b.status === "pending"));
const inspectedBatches = computed(() =>
  [...roster.batches].filter((b) => b.status !== "pending").sort((a, b2) => a.id - b2.id)
);
const selectedBatch = computed(() =>
  roster.batches.find((b) => String(b.id) === String(batchId.value)) || null
);
const bindingSamples = computed(() =>
  roster.bindings.map((g) => ({
    ...g,
    key: g.id,
    batch_no: roster.batches.find((b) => b.id === g.batch_id)?.batch_no || `#${g.batch_id}`,
  }))
);
function bindingsOf(id) {
  return roster.bindings.filter((g) => g.batch_id === id).map((g) => g.string_code);
}
function fmt(t) {
  return t ? new Date(t).toLocaleString("zh-CN", { hour12: false }) : "—";
}
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
async function refresh() {
  if (!session.value) return;
  const [r1, r2] = await Promise.all([
    fetch("/api/logs", { headers: headers() }),
    fetch("/api/batches", { headers: headers() }),
  ]);
  if (r1.status === 401 || r2.status === 401) { logout(); return; }
  if (r1.ok) logs.value = await r1.json();
  if (r2.ok) Object.assign(roster, await r2.json());
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refresh();
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  localStorage.removeItem("pv_session");
}
async function callApi(url, body, errSlot = error) {
  errSlot.value = "";
  loading.value = true;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: body === undefined ? null : JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { errSlot.value = data.detail || "操作失败"; return false; }
    if (data && data.batches) Object.assign(roster, data);
    await refresh();
    return true;
  } catch { errSlot.value = "网络异常"; return false; }
  finally { loading.value = false; }
}
async function submit() {
  const ok = await callApi("/api/logs", {
    batch_id: batchId.value === "" ? null : Number(batchId.value),
    string_code: stringCode.value,
    voc_v: Number(voc.value),
    isc_a: Number(isc.value),
    fill_factor: Number(ff.value),
  });
  if (ok) {
    stringCode.value = voc.value = isc.value = ff.value = "";
    batchId.value = "";
  }
}
async function createBatch() {
  const no = newBatchNo.value.trim();
  if (!no) { rosterError.value = "批次号不能为空"; return; }
  if (await callApi(`/api/batches`, { batch_no: no }, rosterError)) newBatchNo.value = "";
}
function inspect(id) { return callApi(`/api/batches/${id}/inspect`, undefined, rosterError); }
function voidBatch(id) { return callApi(`/api/batches/${id}/void`, undefined, rosterError); }
async function bind(id) {
  const code = (bindInput[id] || "").trim();
  if (!code) { rosterError.value = "组串编号不能为空"; return; }
  if (await callApi(`/api/batches/${id}/bindings`, { string_code: code }, rosterError))
    bindInput[id] = "";
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { font-size: 1.05rem; margin: 0 0 0.75rem; color: #86efac; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar button.active { background: #22c55e; color: #052e16; }
.topbar .spacer { flex: 1; }
.topbar .who { color: #a7f3d0; font-size: 0.9rem; margin-right: 0.5rem; }
.rowhead { display: flex; align-items: center; justify-content: space-between; }
.cols { display: flex; gap: 1rem; align-items: flex-start; }
.col { flex: 1; min-width: 0; }
.card { background: #0f3d24; border: 1px solid #166534; border-radius: 6px; padding: 0.7rem 0.8rem; margin-bottom: 0.7rem; }
.card.voided { opacity: 0.65; }
.card-title { font-weight: 600; margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.4rem; }
.muted { color: #86efac; font-size: 0.8rem; margin-bottom: 0.5rem; }
.empty { color: #fde68a; background: #854d0e55; border-radius: 6px; padding: 0.6rem; font-size: 0.85rem; }
.mini { display: flex; gap: 0.4rem; margin: 0.5rem 0; }
.mini input { margin-bottom: 0; }
.chip { display: inline-block; background: #166534; border-radius: 4px; padding: 0.05rem 0.45rem; margin-right: 0.3rem; font-size: 0.8rem; }
.hint { font-size: 0.85rem; color: #a7f3d0; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
select option:disabled { color: #fca5a5; }
.mini input { width: auto; flex: 1; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
@media (max-width: 860px) { .cols { flex-direction: column; } .col { width: 100%; } }
</style>
