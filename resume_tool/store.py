import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .schema import derived, statistics, normalize_candidate


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "resumes.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, title TEXT, status TEXT, created TEXT, model TEXT, is_demo INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), filename TEXT, path TEXT,
                digest TEXT, status TEXT, data TEXT, raw_text TEXT DEFAULT '', error TEXT DEFAULT '', method TEXT DEFAULT '',
                duplicate_of TEXT DEFAULT '', usage TEXT DEFAULT '{}', edited INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_candidates_job ON candidates(job_id);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)")}
            for name, definition in (("remark", "TEXT NOT NULL DEFAULT ''"),
                                     ("short_remark", "TEXT NOT NULL DEFAULT ''"),
                                     ("remark_color", "TEXT NOT NULL DEFAULT '#18594c'")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {definition}")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS talent (
                id TEXT PRIMARY KEY, source_row_id TEXT, source_job_id TEXT DEFAULT '',
                source_title TEXT DEFAULT '', filename TEXT DEFAULT '', data TEXT NOT NULL,
                raw_text TEXT DEFAULT '', remark TEXT DEFAULT '', short_remark TEXT DEFAULT '',
                remark_color TEXT DEFAULT '#18594c', edited INTEGER DEFAULT 0,
                created TEXT NOT NULL, updated TEXT NOT NULL, deleted_at TEXT DEFAULT '');
            CREATE INDEX IF NOT EXISTS idx_talent_deleted ON talent(deleted_at);
            CREATE INDEX IF NOT EXISTS idx_talent_source ON talent(source_row_id);
            """)
            # Import older successful real resumes exactly once; demo rows stay separate.
            old_rows = conn.execute("SELECT c.id FROM candidates c JOIN jobs j ON j.id=c.job_id WHERE c.status='done' AND j.is_demo=0").fetchall()
            for row in old_rows:
                self._sync_talent(conn, row["id"], overwrite=False)

    def _sync_talent(self, conn, row_id, overwrite=True):
        row = conn.execute("SELECT c.*,j.title AS source_title,j.created AS batch_created,j.is_demo FROM candidates c JOIN jobs j ON j.id=c.job_id WHERE c.id=?", (row_id,)).fetchone()
        if not row or row["status"] != "done" or row["is_demo"] or not row["data"] or row["data"] == "null":
            return
        columns = ("id", "source_row_id", "source_job_id", "source_title", "filename", "data", "raw_text", "remark", "short_remark", "remark_color", "edited", "created", "updated")
        values = (row_id, row_id, row["job_id"], row["source_title"], row["filename"], row["data"], row["raw_text"], row["remark"], row["short_remark"], row["remark_color"], row["edited"], row["batch_created"], now())
        statement = "INSERT INTO talent(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")"
        if overwrite:
            statement += " ON CONFLICT(id) DO UPDATE SET " + ",".join(key + "=excluded." + key for key in columns if key not in {"id", "source_row_id", "created"})
        else:
            statement = statement.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
        conn.execute(statement, values)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE candidates SET status='failed',error='程序已重启，请重试未完成的文件' WHERE status IN ('queued','processing')")
            conn.execute("UPDATE jobs SET status='completed' WHERE status IN ('running','canceling')")

    def create_job(self, job_id, title, model, rows, demo=False):
        with self.connect() as conn:
            conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (job_id, title, "completed" if demo else "running", now(), model, int(demo)))
            for row in rows:
                conn.execute("INSERT INTO candidates(id,job_id,filename,path,digest,status,data,raw_text,duplicate_of,method,remark,short_remark,remark_color) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (row["id"], job_id, row["filename"], row.get("path", ""), row.get("digest", ""), row.get("status", "queued"),
                              json.dumps(row.get("data"), ensure_ascii=False), row.get("raw_text", ""), row.get("duplicate_of", ""), row.get("method", ""), row.get("remark", ""), row.get("short_remark", ""), row.get("remark_color", "#18594c")))
                self._sync_talent(conn, row["id"])

    def get_job(self, job_id, full=True):
        with self.connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError("批次不存在")
            rows = conn.execute("SELECT * FROM candidates WHERE job_id=? ORDER BY rowid", (job_id,)).fetchall()
        result = dict(job)
        result["rows"] = []
        for row in rows:
            item = dict(row)
            item.pop("path", None)
            item.pop("digest", None)
            if not full:
                item.pop("raw_text", None)
            saved_data = json.loads(item["data"]) if item["data"] else None
            item["data"] = normalize_candidate(saved_data) if saved_data is not None else None
            item["usage"] = json.loads(item["usage"])
            item["derived"] = derived(item["data"]) if item["data"] else None
            result["rows"].append(item)
        # Flag contact duplicates without hiding distinct versions of a resume.
        seen = {}
        for row in result["rows"]:
            if row["status"] != "done":
                continue
            data = row["data"]
            contacts = ["phone:" + data["phone"].replace(" ", "") if data["phone"] else "",
                        "email:" + data["email"].casefold() if data["email"] else ""]
            for contact in filter(None, contacts):
                if contact in seen and seen[contact] != row["id"]:
                    warning = "联系方式与本批次另一份简历相同，请核对是否为同一候选人"
                    if warning not in row["derived"]["warnings"]:
                        row["derived"]["warnings"].append(warning)
                seen.setdefault(contact, row["id"])
        result["stats"] = statistics(result["rows"])
        result["tokens"] = sum(row["usage"].get("total_tokens", 0) for row in result["rows"])
        return result

    def list_jobs(self):
        with self.connect() as conn:
            jobs = conn.execute("SELECT id,title,status,created,is_demo FROM jobs ORDER BY rowid DESC LIMIT 100").fetchall()
        return [dict(row) for row in jobs]

    def source_rows(self, job_id):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM candidates WHERE job_id=? ORDER BY rowid", (job_id,))]

    def update_row(self, row_id, **values):
        allowed = {"status", "data", "raw_text", "error", "method", "usage", "edited", "remark", "short_remark", "remark_color"}
        if not values.keys() <= allowed:
            raise ValueError("不支持的数据字段")
        values = {key: json.dumps(value, ensure_ascii=False) if key in {"data", "usage"} else value for key, value in values.items()}
        with self.connect() as conn:
            conn.execute("UPDATE candidates SET " + ",".join(key + "=?" for key in values) + " WHERE id=?", (*values.values(), row_id))
            self._sync_talent(conn, row_id)

    @staticmethod
    def talent_row(row, full=False):
        item = dict(row)
        item["data"] = normalize_candidate(json.loads(item["data"]))
        item["derived"] = derived(item["data"])
        item.update(status="done", error="", duplicate_of="", usage={}, method="手动录入" if not item["source_row_id"] else "简历自动入库")
        if not full:
            item.pop("raw_text", None)
        return item

    def list_talent(self, query="", degree="", page=1, page_size=25, deleted=False, all_rows=False):
        query = query.strip()[:200]
        page_size = max(1, min(50, int(page_size)))
        conditions = ["deleted_at<>''" if deleted else "deleted_at=''"]
        args = []
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append("(data LIKE ? ESCAPE '\\' OR short_remark LIKE ? ESCAPE '\\' OR remark LIKE ? ESCAPE '\\' OR filename LIKE ? ESCAPE '\\' OR source_title LIKE ? ESCAPE '\\')")
            args.extend([pattern] * 5)
        with self.connect() as conn:
            result = conn.execute("SELECT * FROM talent WHERE " + " AND ".join(conditions) + " ORDER BY created DESC,rowid DESC", args).fetchall()
            total = conn.execute("SELECT count(*) FROM talent WHERE deleted_at='' ").fetchone()[0]
            removed = conn.execute("SELECT count(*) FROM talent WHERE deleted_at<>''").fetchone()[0]
            manual = conn.execute("SELECT count(*) FROM talent WHERE deleted_at='' AND source_row_id IS NULL").fetchone()[0]
        rows = [self.talent_row(row) for row in result]
        if degree:
            rows = [row for row in rows if row["derived"]["highest_degree"] == degree]
        seen = {}
        for row in rows:
            for value in (row["data"]["phone"].replace(" ", ""), row["data"]["email"].casefold()):
                if not value:
                    continue
                if value in seen:
                    warning = "数据库中存在相同联系方式，请核对是否为同一候选人的不同简历版本"
                    for match in (row, seen[value]):
                        if warning not in match["derived"]["warnings"]:
                            match["derived"]["warnings"].append(warning)
                else:
                    seen[value] = row
        matched = len(rows)
        pages = max(1, (matched + page_size - 1) // page_size)
        page = max(1, min(int(page), pages))
        return {"rows": rows if all_rows else rows[(page-1)*page_size:page*page_size], "total": total,
                "matched": matched, "manual": manual, "deleted_count": removed, "page": page, "pages": pages,
                "page_size": page_size, "database_path": str(self.db)}

    def get_talent(self, talent_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM talent WHERE id=?", (talent_id,)).fetchone()
            if not row:
                raise KeyError(talent_id)
            result = self.talent_row(row, full=True)
            source = conn.execute("SELECT j.status FROM candidates c JOIN jobs j ON j.id=c.job_id WHERE c.id=?", (row["source_row_id"],)).fetchone()
            result["can_edit"] = not row["deleted_at"] and (not source or source["status"] == "completed")
            return result

    def talent_counts(self):
        with self.connect() as conn:
            row = conn.execute("SELECT count(CASE WHEN deleted_at='' THEN 1 END) AS total,count(CASE WHEN deleted_at<>'' THEN 1 END) AS deleted_count FROM talent").fetchone()
            return dict(row)

    def create_talent(self, data, remarks):
        candidate = normalize_candidate(data)
        if not candidate["name"]:
            raise ValueError("手动新增候选人请填写姓名")
        talent_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO talent(id,source_title,filename,data,remark,short_remark,remark_color,edited,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (talent_id, "手动录入", "手动录入", json.dumps(candidate, ensure_ascii=False), remarks.get("remark", ""), remarks.get("short_remark", ""), remarks.get("remark_color", "#18594c"), 1, now(), now()))
        return talent_id

    def update_talent(self, talent_id, data=None, **remarks):
        if not set(remarks) <= {"remark", "short_remark", "remark_color"}:
            raise ValueError("不支持的备注字段")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM talent WHERE id=?", (talent_id,)).fetchone()
            if not row:
                raise KeyError(talent_id)
            if row["deleted_at"]:
                raise ValueError("请先从回收站恢复候选人再编辑")
            values = dict(remarks)
            if data is not None:
                source = conn.execute("SELECT j.status FROM candidates c JOIN jobs j ON j.id=c.job_id WHERE c.id=?", (row["source_row_id"],)).fetchone()
                if source and source["status"] != "completed":
                    raise ValueError("原批次正在更新，请完成后再修改候选人信息；备注仍可单独编辑")
                values["data"] = json.dumps(normalize_candidate(data), ensure_ascii=False)
                values["edited"] = int(bool(row["edited"] or json.loads(values["data"]) != normalize_candidate(json.loads(row["data"]))))
            if not values:
                raise ValueError("没有需要保存的内容")
            conn.execute("UPDATE talent SET " + ",".join(key + "=?" for key in values) + ",updated=? WHERE id=?", (*values.values(), now(), talent_id))
            # Keep the original batch view consistent while its source still exists.
            conn.execute("UPDATE candidates SET " + ",".join(key + "=?" for key in values) + " WHERE id=?", (*values.values(), row["source_row_id"]))

    def remove_talent(self, talent_id, restore=False):
        with self.connect() as conn:
            result = conn.execute("UPDATE talent SET deleted_at=?,updated=? WHERE id=?", ("" if restore else now(), now(), talent_id))
            if not result.rowcount:
                raise KeyError(talent_id)

    def set_status(self, job_id, status):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))

    def delete_job(self, job_id):
        with self.connect() as conn:
            conn.execute("DELETE FROM candidates WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
