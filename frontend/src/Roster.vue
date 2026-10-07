<template>
  <div class="roster">
    <section v-if="canWrite" class="register">
      <h3>到货登记</h3>
      <p class="hint">新批次到货先进「待抽检」，抽检合格并绑定组串后，扫描台才能点到它。</p>
      <div class="row">
        <input v-model="newNo" placeholder="批次号，例如 丁批-007" autocomplete="off" />
        <input v-model="newNote" placeholder="备注（可空）" autocomplete="off" />
        <button :disabled="busy" @click="register">登记到货</button>
      </div>
    </section>

    <div class="cols">
      <section class="col">
        <h3>待抽检 <span class="count">{{ pending.length }}</span></h3>
        <p v-if="!pending.length" class="empty">还没有待抽检批次</p>
        <article v-for="b in pending" :key="b.id" class="card">
          <div class="card-head">
            <strong>{{ b.batch_no }}</strong>
            <span class="tag pending">待抽检</span>
          </div>
          <p class="meta">{{ b.note || "无备注" }}</p>
          <p class="meta">登记：{{ fmt(b.created_at) }}</p>
          <div v-if="canWrite" class="actions">
            <button :disabled="busy" @click="$emit('inspect', b.id)">抽检合格</button>
            <button class="danger" :disabled="busy" @click="voidIt(b)">作废</button>
          </div>
        </article>
      </section>

      <section class="col">
        <h3>已抽检 <span class="count">{{ inspected.length }}</span></h3>
        <p v-if="!inspected.length" class="empty">还没有抽检记录</p>
        <article v-for="b in inspected" :key="b.id" class="card">
          <div class="card-head">
            <strong>{{ b.batch_no }}</strong>
            <span class="tag ok">已抽检</span>
          </div>
          <p class="meta">抽检：{{ b.inspected_by }} · {{ fmt(b.inspected_at) }}</p>
          <p v-if="b.bound_string" class="meta">
            已绑定：<b>{{ b.bound_string }}</b>
          </p>
          <template v-else>
            <p class="meta">尚未绑定组串，扫描台还不能收这一批。</p>
            <div v-if="canWrite" class="row">
              <input
                v-model="bindInputs[b.id]"
                placeholder="绑定组串编号，例如 阵列A-串03"
                autocomplete="off"
              />
              <button :disabled="busy" @click="bindIt(b)">绑定样例</button>
            </div>
          </template>
          <div v-if="canWrite" class="actions">
            <button class="danger" :disabled="busy" @click="voidIt(b)">作废批次</button>
          </div>
        </article>
      </section>

      <section class="col">
        <h3>绑定样例 <span class="count">{{ bound.length }}</span></h3>
        <p class="hint">批次绑上单据后，批次号即冻进单据；批次之后作废，旧单仍印着原批次号。</p>
        <p v-if="!bound.length" class="empty">还没有抽检记录</p>
        <table v-else class="bind-table">
          <thead>
            <tr><th>批次</th><th>绑定组串</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="b in bound" :key="b.id">
              <td>{{ b.batch_no }}</td>
              <td>{{ b.bound_string }}</td>
              <td>
                <span class="tag" :class="b.status === 'voided' ? 'bad' : 'ok'">
                  {{ b.status === "voided" ? "已作废（旧单冻结）" : "在用" }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from "vue";

const props = defineProps({
  batches: { type: Array, required: true },
  canWrite: { type: Boolean, required: true },
  busy: { type: Boolean, default: false },
});
const emit = defineEmits(["register", "inspect", "bind", "void"]);

const newNo = ref("");
const newNote = ref("");
const bindInputs = reactive({});

const pending = computed(() => props.batches.filter((b) => b.status === "pending"));
const inspected = computed(() => props.batches.filter((b) => b.status === "inspected"));
const bound = computed(
  () => props.batches.filter((b) => b.bound_string)
    .slice()
    .sort((a, b2) => (b2.bound_at || "").localeCompare(a.bound_at || ""))
);

function fmt(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("zh-CN", { hour12: false });
}

function register() {
  const no = newNo.value.trim();
  if (!no) return;
  emit("register", { batch_no: no, note: newNote.value.trim() });
  newNo.value = "";
  newNote.value = "";
}

function bindIt(b) {
  const code = (bindInputs[b.id] || "").trim();
  emit("bind", { id: b.id, string_code: code });
}

function voidIt(b) {
  if (window.confirm(`确认作废批次 ${b.batch_no}？旧单据上的批次号会保持冻住。`)) {
    emit("void", b.id);
  }
}
</script>

<style scoped>
.register h3, .col h3 { margin: 0 0 0.5rem; color: #bbf7d0; }
.cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
.col { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem; }
.count { font-size: 0.8rem; background: #166534; border-radius: 10px; padding: 0.05rem 0.5rem; }
.card { background: #052e16; border: 1px solid #166534; border-radius: 6px; padding: 0.7rem; margin-bottom: 0.7rem; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
.meta { font-size: 0.8rem; color: #a7f3d0; margin: 0.3rem 0; }
.actions { margin-top: 0.5rem; }
.row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.row input { flex: 1; min-width: 10rem; margin-bottom: 0; }
.hint { font-size: 0.8rem; color: #a7f3d0; }
.empty { color: #fde68a; font-size: 0.9rem; background: #052e16; border: 1px dashed #854d0e; border-radius: 6px; padding: 0.8rem; text-align: center; }
.bind-table { width: 100%; font-size: 0.85rem; }
button.danger { background: #7f1d1d; }
</style>
