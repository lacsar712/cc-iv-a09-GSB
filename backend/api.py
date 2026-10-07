import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext
import psycopg

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        now = datetime.now(timezone.utc)

        def get_batch(no):
            return conn.execute(
                "SELECT id, status FROM batches WHERE batch_no=%s", (no,)
            ).fetchone()

        if get_batch("丙批-001") is None:
            conn.execute(
                """INSERT INTO batches (batch_no, status, note, created_by, created_at)
                   VALUES ('丙批-001','pending','新到货，等待抽检','scanner',%s)""",
                (now,),
            )

        def ensure_inspected_bound(no, string):
            row = get_batch(no)
            if row is None:
                conn.execute(
                    """INSERT INTO batches
                       (batch_no, status, note, created_by, created_at,
                        inspected_by, inspected_at)
                       VALUES (%s,'inspected','到货抽检合格','scanner',%s,'scanner',%s)
                       RETURNING id""",
                    (no, now, now),
                )
                bid = conn.execute(
                    "SELECT id FROM batches WHERE batch_no=%s", (no,)
                ).fetchone()["id"]
                conn.execute(
                    """INSERT INTO batch_bindings
                       (batch_id, string_code, bound_by, bound_at)
                       VALUES (%s,%s,'scanner',%s)""",
                    (bid, string, now),
                )

        ensure_inspected_bound("甲批-001", "阵列A-串03")
        ensure_inspected_bound("乙批-001", "阵列B-串11")

        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格", "甲批-001"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减", "乙批-001"),
            ]
            for code, voc, isc, ff, expect, batch_no in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                bid = get_batch(batch_no)["id"]
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at, batch_id, batch_no)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s,%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now, bid, batch_no),
                )
            # 乙批后来作废：新单据一律被挡，旧单据上冻住的批次号不变。
            conn.execute(
                """UPDATE batches
                   SET status='voided', voided_by='scanner', voided_at=%s
                   WHERE batch_no='乙批-001'""",
                (now,),
            )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request, action: str = "此操作"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=f"观察员只读，{action}需扫描员权限")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at, batch_id, batch_no
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request, "提交IV扫描")
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    # 录入必须点选已抽检批次；漏点整笔退，服务端绝不替输入偷偷换批次。
    batch_id = data.get("batch_id")
    if batch_id is None or str(batch_id).strip() == "":
        raise HTTPException(status_code=400, detail="必须点选一个已抽检批次，整笔未入队")
    try:
        batch_id = int(batch_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="批次号无效，整笔未入队")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            with conn.transaction():
                batch = conn.execute(
                    "SELECT id, batch_no, status FROM batches WHERE id=%s FOR UPDATE",
                    (batch_id,),
                ).fetchone()
                if batch is None:
                    raise HTTPException(status_code=400, detail="批次不存在，整笔未入队")
                if batch["status"] == "pending":
                    raise HTTPException(
                        status_code=400,
                        detail=f"批次 {batch['batch_no']} 尚未完成到货抽检，整笔退回",
                    )
                if batch["status"] == "voided":
                    raise HTTPException(
                        status_code=400,
                        detail=f"批次 {batch['batch_no']} 已作废，整笔退回",
                    )
                binding = conn.execute(
                    "SELECT string_code FROM batch_bindings WHERE batch_id=%s FOR UPDATE",
                    (batch_id,),
                ).fetchone()
                if binding is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"批次 {batch['batch_no']} 还未绑定组串，整笔退回",
                    )
                if binding["string_code"] != code:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"批次 {batch['batch_no']} 绑定的是 {binding['string_code']}，"
                            f"不能开在 {code} 的单据上，整笔退回"
                        ),
                    )
                row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, created_by,
                        created_at, batch_id)
                       VALUES (%s,%s,%s,%s,'pending',%s,%s,%s)
                       RETURNING id, string_code, voc_v, isc_a, fill_factor, status,
                                 verdict, reason, created_by, created_at, processed_at,
                                 batch_id, batch_no""",
                    (code, voc, isc, ff, user["username"], now, batch_id),
                ).fetchone()
            conn.commit()
        except HTTPException:
            raise
        except psycopg.Error as exc:
            # 数据库闸门是最后防线：任何绕过接口直写的尝试同样被整笔拒绝。
            raise HTTPException(status_code=400, detail=str(exc.diag.message_primary or exc))
        return dump(row)


BATCH_SELECT = """
SELECT b.id, b.batch_no, b.status, b.note, b.created_by, b.created_at,
       b.inspected_by, b.inspected_at, b.voided_by, b.voided_at,
       bb.string_code AS bound_string, bb.bound_at,
       (SELECT COUNT(*) FROM iv_scans s WHERE s.batch_id = b.id) AS doc_count
FROM batches b
LEFT JOIN batch_bindings bb ON bb.batch_id = b.id
ORDER BY b.id
"""


@get("/api/batches")
async def list_batches(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        return [dump(r) for r in conn.execute(BATCH_SELECT).fetchall()]


@post("/api/batches", status_code=201)
async def register_batch(request: Request) -> dict:
    user = need_writer(request, "登记到货批次")
    data = await request.json()
    batch_no = (data.get("batch_no") or "").strip()
    if not batch_no:
        raise HTTPException(status_code=400, detail="批次号不能为空")
    note = (data.get("note") or "").strip() or None
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            with conn.transaction():
                row = conn.execute(
                    """INSERT INTO batches (batch_no, status, note, created_by, created_at)
                       VALUES (%s,'pending',%s,%s,%s)
                       RETURNING id, batch_no, status""",
                    (batch_no, note, user["username"], now),
                ).fetchone()
            conn.commit()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=400, detail=f"批次 {batch_no} 已存在")
        return dump(row)


def _load_batch(conn, batch_id: int):
    batch = conn.execute(
        "SELECT id, batch_no, status FROM batches WHERE id=%s FOR UPDATE",
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


@post("/api/batches/{batch_id:int}/inspect")
async def inspect_batch(request: Request, batch_id: int) -> dict:
    user = need_writer(request, "抽检确认")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        with conn.transaction():
            batch = _load_batch(conn, batch_id)
            if batch["status"] != "pending":
                raise HTTPException(
                    status_code=400,
                    detail=f"批次 {batch['batch_no']} 当前不可抽检（{batch['status']}）",
                )
            conn.execute(
                """UPDATE batches
                   SET status='inspected', inspected_by=%s, inspected_at=%s
                   WHERE id=%s""",
                (user["username"], now, batch_id),
            )
        conn.commit()
        return {"ok": True}


@post("/api/batches/{batch_id:int}/bind")
async def bind_batch(request: Request, batch_id: int) -> dict:
    user = need_writer(request, "绑定组串")
    data = await request.json()
    string_code = (data.get("string_code") or "").strip()
    if not string_code:
        raise HTTPException(status_code=400, detail="绑定的组串编号不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            with conn.transaction():
                batch = _load_batch(conn, batch_id)
                if batch["status"] != "inspected":
                    raise HTTPException(
                        status_code=400,
                        detail=f"批次 {batch['batch_no']} 未通过抽检，不能绑定",
                    )
                existing = conn.execute(
                    "SELECT string_code FROM batch_bindings WHERE batch_id=%s",
                    (batch_id,),
                ).fetchone()
                if existing is not None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"批次 {batch['batch_no']} 已绑定 {existing['string_code']}",
                    )
                conn.execute(
                    """INSERT INTO batch_bindings
                       (batch_id, string_code, bound_by, bound_at)
                       VALUES (%s,%s,%s,%s)""",
                    (batch_id, string_code, user["username"], now),
                )
            conn.commit()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=400, detail="该批次已绑定组串")
        return {"ok": True}


@post("/api/batches/{batch_id:int}/void")
async def void_batch(request: Request, batch_id: int) -> dict:
    user = need_writer(request, "作废批次")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        with conn.transaction():
            batch = _load_batch(conn, batch_id)
            if batch["status"] == "voided":
                raise HTTPException(
                    status_code=400, detail=f"批次 {batch['batch_no']} 已是作废状态"
                )
            # 已开过单据也允许作废：旧单据上的批次号保持冻住，不回写、不替换。
            conn.execute(
                """UPDATE batches
                   SET status='voided', voided_by=%s, voided_at=%s
                   WHERE id=%s""",
                (user["username"], now, batch_id),
            )
        conn.commit()
        return {"ok": True}


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        list_batches,
        register_batch,
        inspect_batch,
        bind_batch,
        void_batch,
    ]
)
