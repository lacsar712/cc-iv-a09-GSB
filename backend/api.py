import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

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
        bn = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if bn == 0:
            # 批次甲：已完成到货抽检并绑定阵列A-串03；批次乙：尚未抽检。
            conn.execute(
                """INSERT INTO batches (batch_no, status, inspected_at, inspected_by, created_at)
                   VALUES ('批次甲', 'inspected', %s, 'scanner', %s)""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO batches (batch_no, status, created_at)
                   VALUES ('批次乙', 'pending', %s)""",
                (now,),
            )
            jia = conn.execute(
                "SELECT id FROM batches WHERE batch_no='批次甲'"
            ).fetchone()
            conn.execute(
                """INSERT INTO batch_bindings (batch_id, string_code, created_by, created_at)
                   VALUES (%s, '阵列A-串03', 'scanner', %s)""",
                (jia["id"], now),
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            jia = conn.execute(
                "SELECT id FROM batches WHERE batch_no='批次甲'"
            ).fetchone()
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                # 旧单上批次号取快照，后续批次作废也不改写单据。
                batch_id = jia["id"] if code == "阵列A-串03" else None
                batch_no = "批次甲" if batch_id else None
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at, batch_id, batch_no)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s,%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now, batch_id, batch_no),
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
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=f"观察员只读，无权{action}")
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


def fetch_roster(conn):
    batches = [
        dump(r)
        for r in conn.execute(
            """SELECT id, batch_no, status, inspected_at, inspected_by,
                      voided_at, voided_by, created_at
               FROM batches ORDER BY id"""
        ).fetchall()
    ]
    bindings = [
        dump(r)
        for r in conn.execute(
            """SELECT id, batch_id, string_code, created_by, created_at
               FROM batch_bindings ORDER BY id"""
        ).fetchall()
    ]
    return {"batches": batches, "bindings": bindings}


@get("/api/batches")
async def list_batches(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        return fetch_roster(conn)


@post("/api/batches", status_code=201)
async def create_batch(request: Request) -> dict:
    user = need_writer(request, "登记到货批次")
    data = await request.json()
    batch_no = (data.get("batch_no") or "").strip()
    if not batch_no:
        raise HTTPException(status_code=400, detail="批次号不能为空")
    with connect() as conn:
        if conn.execute(
            "SELECT 1 FROM batches WHERE batch_no=%s", (batch_no,)
        ).fetchone():
            raise HTTPException(status_code=400, detail="批次号已存在")
        conn.execute(
            """INSERT INTO batches (batch_no, status, created_at)
               VALUES (%s, 'pending', %s)""",
            (batch_no, datetime.now(timezone.utc)),
        )
        conn.commit()
        return fetch_roster(conn)


def load_batch(conn, batch_id: int):
    batch = conn.execute(
        "SELECT id, batch_no, status FROM batches WHERE id=%s FOR UPDATE",
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


@post("/api/batches/{batch_id:int}/inspect")
async def inspect_batch(request: Request, batch_id: int) -> dict:
    user = need_writer(request, "录入抽检")
    with connect() as conn:
        with conn.transaction():
            batch = load_batch(conn, batch_id)
            if batch["status"] == "inspected":
                raise HTTPException(status_code=409, detail="该批次已抽检，无需重复录入")
            if batch["status"] == "void":
                raise HTTPException(status_code=409, detail="已作废批次不能再录抽检")
            conn.execute(
                """UPDATE batches
                   SET status='inspected', inspected_at=%s, inspected_by=%s
                   WHERE id=%s""",
                (datetime.now(timezone.utc), user["username"], batch_id),
            )
        conn.commit()
        return fetch_roster(conn)


@post("/api/batches/{batch_id:int}/void")
async def void_batch(request: Request, batch_id: int) -> dict:
    user = need_writer(request, "作废批次")
    with connect() as conn:
        with conn.transaction():
            batch = load_batch(conn, batch_id)
            if batch["status"] == "void":
                raise HTTPException(status_code=409, detail="该批次已是作废状态")
            if batch["status"] != "inspected":
                raise HTTPException(status_code=409, detail="只有已抽检批次可以作废")
            conn.execute(
                """UPDATE batches
                   SET status='void', voided_at=%s, voided_by=%s
                   WHERE id=%s""",
                (datetime.now(timezone.utc), user["username"], batch_id),
            )
        conn.commit()
        return fetch_roster(conn)


@post("/api/batches/{batch_id:int}/bindings", status_code=201)
async def bind_string(request: Request, batch_id: int) -> dict:
    user = need_writer(request, "绑定组串")
    data = await request.json()
    string_code = (data.get("string_code") or "").strip()
    if not string_code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    with connect() as conn:
        with conn.transaction():
            batch = load_batch(conn, batch_id)
            if batch["status"] != "inspected":
                if batch["status"] == "void":
                    raise HTTPException(status_code=409, detail="批次已作废，不能再绑定组串")
                raise HTTPException(status_code=409, detail="批次尚未完成抽检，不能绑定组串")
            exists = conn.execute(
                "SELECT 1 FROM batch_bindings WHERE batch_id=%s AND string_code=%s",
                (batch_id, string_code),
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO batch_bindings (batch_id, string_code, created_by, created_at)
                       VALUES (%s,%s,%s,%s)""",
                    (batch_id, string_code, user["username"], datetime.now(timezone.utc)),
                )
        conn.commit()
        return fetch_roster(conn)


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
    # 批次闸门：漏选 / 未抽检 / 已作废 / 未绑定本组串，一律整笔退回，不落库。
    batch_id = data.get("batch_id")
    if batch_id is None or str(batch_id).strip() == "":
        raise HTTPException(status_code=400, detail="必须选择已抽检批次，整笔退回")
    try:
        batch_id = int(batch_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="批次号无效，整笔退回")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        with conn.transaction():
            # 服务端按库里的实时状态核验，前端隐藏按钮或改写请求都绕不过去。
            batch = conn.execute(
                "SELECT id, batch_no, status FROM batches WHERE id=%s FOR UPDATE",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise HTTPException(status_code=400, detail="批次不存在，整笔退回")
            if batch["status"] == "pending":
                raise HTTPException(
                    status_code=400,
                    detail=f"批次 {batch['batch_no']} 未完成到货抽检，整笔退回",
                )
            if batch["status"] == "void":
                raise HTTPException(
                    status_code=400,
                    detail=f"批次 {batch['batch_no']} 已作废，整笔退回",
                )
            bound = conn.execute(
                "SELECT 1 FROM batch_bindings WHERE batch_id=%s AND string_code=%s",
                (batch_id, code),
            ).fetchone()
            if bound is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"批次 {batch['batch_no']} 未绑定组串 {code}，整笔退回",
                )
            # 批次号在此刻快照入单据；批次日后作废，旧单仍印这个号。
            row = conn.execute(
                """INSERT INTO iv_scans
                   (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at,
                    batch_id, batch_no)
                   VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,%s)
                   RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                             created_by, created_at, processed_at, batch_id, batch_no""",
                (code, voc, isc, ff, user["username"], now, batch_id, batch["batch_no"]),
            ).fetchone()
        conn.commit()
        return dump(row)


app = Litestar(
    route_handlers=[
        health,
        login,
        list_batches,
        create_batch,
        inspect_batch,
        void_batch,
        bind_string,
        list_logs,
        create_log,
    ]
)
