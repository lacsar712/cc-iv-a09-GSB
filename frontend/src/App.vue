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
        <button :class="{ active: page === 'scan' }" @click="page = 'scan'">IV 扫描</button>
        <button :class="{ active: page === 'roster' }" @click="page = 'roster'">抽检名册</button>
        <span class="who">
          {{ session.username }}（{{ isWriter ? "扫描员·可写" : "观察员·只读" }}）
        </span>
        <button class="secondary" @click="refreshAll">刷新</button>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <div v-if="page === 'scan'">
        <section v-if="isWriter">
          <h3>提交扫描</h3>
          <p class="hint">组串没有完成到货抽检就不能入队：批次必须从下方「已抽检且已绑定」清单中点选。</p>
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <label>到货批次（必须点选已抽检批次）</label>
          <select v-model="batchId">
            <option value="">— 请选择已抽检且已绑定本组串的批次 —</option>
            <option v-for="b in usableBatches" :key="b.id" :value="b.id">
              {{ b.batch_no }}（绑定 {{ b.bound_string }}）
            </option>
          </select>
          <p v-if="!usableBatches.length" class="hint">当前没有可入队的批次，请到「抽检名册」完成抽检与绑定。</p>
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section v-else>
          <p class="hint">观察员可翻阅名册与单据，但不能登记、抽检、绑定、作废或提交扫描。</p>
        </section>

        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>批次</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.batch_no || "—" }}</td>
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

      <Roster
        v-else
        :batches="batches"
        :can-write="isWriter"
        :busy="loading"
        @register="onRegister"
        @inspect="onInspect"
        @bind="onBind"
        @void="onVoid"
      />
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import Roster from "./Roster.vue";

const session = ref(null);
const logs = ref([]);
const batches = ref([]);
const page = ref("scan");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const batchId = ref("");
const error = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
// 只有「已抽检 + 已绑定」的批次才会出现在录入点选清单里；
// 待抽检、已作废批次根本不在写入通道的可选项中。
const usableBatches = computed(() =>
  batches.value.filter((b) => b.status === "inspected" && b.bound_string)
);

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...headers() },
    ...options,
  });
  if (res.status === 401) { logout(); throw new Error("未登录"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.detail || "请求失败"), { status: res.status });
  return data;
}

async function refreshLogs() {
  if (!session.value) return;
  try { logs.value = await api("/api/logs"); } catch { /* 401 已处理 */ }
}
async function refreshBatches() {
  if (!session.value) return;
  try { batches.value = await api("/api/batches"); } catch { /* 401 已处理 */ }
}
async function refreshAll() {
  await Promise.all([refreshLogs(), refreshBatches()]);
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
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  batches.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  if (batchId.value === "") {
    error.value = "必须点选一个已抽检批次，整笔未提交";
    return;
  }
  loading.value = true;
  try {
    await api("/api/logs", {
      method: "POST",
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
        batch_id: Number(batchId.value),
      }),
    });
    stringCode.value = voc.value = isc.value = ff.value = "";
    batchId.value = "";
    await refreshAll();
  } catch (e) { error.value = e.message; }
  finally { loading.value = false; }
}

async function mut(path, body) {
  error.value = "";
  loading.value = true;
  try {
    await api(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
    await refreshBatches();
  } catch (e) {
    error.value = e.message;
    window.alert(e.message);
  } finally { loading.value = false; }
}
function onRegister(payload) { mut("/api/batches", payload); }
function onInspect(id) { mut(`/api/batches/${id}/inspect`); }
function onBind({ id, string_code }) {
  if (!string_code) { window.alert("请填写要绑定的组串编号"); return; }
  mut(`/api/batches/${id}/bind`, { string_code });
}
function onVoid(id) { mut(`/api/batches/${id}/void`); }

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.75rem; }
.sub, .hint { color: #a7f3d0; }
.hint { font-size: 0.85rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.topbar { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }
.topbar .who { margin-left: auto; color: #a7f3d0; font-size: 0.9rem; }
.topbar button.active { background: #15803d; outline: 2px solid #86efac; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
