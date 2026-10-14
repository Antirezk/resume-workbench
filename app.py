import io
import json
import os
import re
import secrets
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from resume_tool.demo import demo_rows
from resume_tool.extraction import SUPPORTED, extract_text
from resume_tool.llm import validate_config, parse_resume, test_connection
from resume_tool.reports import export_excel, export_pdf
from resume_tool.schema import normalize_candidate, statistics, derived
from resume_tool.store import Store
from resume_tool.paths import RESOURCE_ROOT, default_data_root


ROOT = RESOURCE_ROOT
MAX_FILE = 20 * 1024 * 1024
MAX_BATCH = 120 * 1024 * 1024
MAX_FILES = 50


def clean_remark(value):
    if not isinstance(value, str):
        raise ValueError("候选人备注必须是文字")
    if len(value) > 5000:
        raise ValueError("候选人备注最多 5000 字")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()


def remark_values(payload, prefix=""):
    values = {}
    for key in ("remark", "short_remark", "remark_color"):
        if prefix + key not in payload:
            continue
        value = payload[prefix + key]
        if key == "remark_color":
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError("字体颜色请使用完整的六位颜色值")
            values[key] = value.lower()
        else:
            value = clean_remark(value)
            if key == "short_remark" and (len(value) > 50 or "\n" in value or "\r" in value):
                raise ValueError("短备注最多 50 字，只支持单行文字")
            values[key] = value
    return values


class Manager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.active = None
        self.cancel = threading.Event()

    def reserve(self, job_id):
        with self.lock:
            if self.active is not None:
                raise ValueError("已有批次正在处理，请等待完成或停止后再上传")
            self.active = job_id
            self.cancel.clear()

    def release(self):
        with self.lock:
            self.active = None

    def start(self, job_id, config, row_ids=None, education_only=False):
        thread = threading.Thread(target=self.run, args=(job_id, config, row_ids, education_only), daemon=True)
        thread.start()

    def run(self, job_id, config, row_ids, education_only=False):
        try:
            rows = [row for row in self.store.source_rows(job_id)
                    if row["status"] == "queued" and (row_ids is None or row["id"] in row_ids)]
            with ThreadPoolExecutor(max_workers=max(1, min(3, len(rows)))) as pool:
                list(pool.map(lambda row: self.process(row, config, education_only), rows))
        finally:
            self.store.set_status(job_id, "completed")
            # API credentials were scoped to this batch and are never written to disk.
            self.release()

    def process(self, row, config, education_only=False):
        original = normalize_candidate(json.loads(row["data"])) if education_only else None
        if self.cancel.is_set():
            self.store.update_row(row["id"], status="done" if education_only else "canceled", error="已停止，文件尚未处理")
            return
        self.store.update_row(row["id"], status="processing", error="")
        try:
            extracted = {"text": row["raw_text"], "method": row["method"]} if education_only and row["raw_text"] else extract_text(row["path"], config["use_ocr"], config["force_ocr"])
            self.store.update_row(row["id"], raw_text=extracted["text"], method=extracted["method"])
            if self.cancel.is_set():
                self.store.update_row(row["id"], status="done" if education_only else "canceled", error="已停止，文件文字已读取但尚未调用 API")
                return
            data, usage = parse_resume(extracted["text"], config)
            if education_only:
                if education_only == "supplement":
                    for section in ("experience", "projects", "qualifications", "languages", "links"):
                        if not row["edited"] or not original[section]:
                            original[section] = data[section]
                        else:
                            # Match stable names before filling blanks; preserve manual content and removals.
                            keys = ("company", "title", "start_date") if section == "experience" else (("label", "url") if section == "links" else ("name",))
                            for old in original[section]:
                                matches = [new for new in data[section] if all(new[k] == old[k] for k in keys)]
                                if len(matches) == 1:
                                    for key, value in matches[0].items():
                                        if key not in {"is_internship", "employment_type"} and not old.get(key):
                                            old[key] = value
                            label = {"experience": "任职经历", "projects": "项目", "qualifications": "证书奖项", "languages": "语言", "links": "主页"}[section]
                            original["notes"].append("已保留人工修改的" + label + "记录；新增或不匹配条目请对照原文核对")
                    for key in ("target_city", "job_status", "availability", "claimed_work_years"):
                        if not original[key]:
                            original[key] = data[key]
                    original["notes"] = list(dict.fromkeys(original["notes"] + data["notes"]))
                    original["evidence"] = list({(e["field"], e["quote"]): e for e in original["evidence"] + data["evidence"]}.values())
                else:
                    original["education"] = data["education"]
                    original["evidence"] = [e for e in original["evidence"] if not e["field"].startswith("education")]
                    original["evidence"].extend({"field": f"education[{i}]", "quote": item["source_text"]} for i, item in enumerate(data["education"]) if item["source_text"])
                    original["notes"] = list(dict.fromkeys(original["notes"] + [note for note in data["notes"] if any(word in note for word in ("教育", "学历", "院校", "专业", "学位"))]))
                data = original
                previous_usage = json.loads(row["usage"])
                usage = {key: usage.get(key, 0) + previous_usage.get(key, 0) for key in usage}
            self.store.update_row(row["id"], status="done", data=data, usage=usage)
        except Exception as error:
            # Do not persist arbitrary third-party exception text containing request credentials.
            message = str(error) if isinstance(error, ValueError) else "文件解析失败，请检查格式、扫描清晰度或模型设置"
            if config["api_key"]:
                message = message.replace(config["api_key"], "[已隐藏]")
            if education_only:
                original["notes"].append(("经历信息补全未完成：" if education_only == "supplement" else "教育信息更新未完成：") + message[:500])
                self.store.update_row(row["id"], status="done", data=original, error=message[:500])
            else:
                self.store.update_row(row["id"], status="failed", error=message[:500])


def uploaded_files(enforce_limit=True, inspect_only=False):
    entries, total = [], 0
    for file in request.files.getlist("files"):
        filename = re.sub(r"[\x00-\x1f]", "", (file.filename or "").replace("\\", "/").split("/")[-1])[:180]
        content = file.read(MAX_FILE + 1 if not filename.lower().endswith(".zip") else MAX_BATCH + 1)
        if filename.lower().endswith(".zip"):
            if len(content) > MAX_BATCH:
                raise ValueError("ZIP 超过 120 MB")
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for info in archive.infolist():
                        suffix = Path(info.filename).suffix.lower()
                        if info.is_dir() or suffix not in SUPPORTED or "__MACOSX" in info.filename:
                            continue
                        if info.flag_bits & 1:
                            raise ValueError("ZIP 含有加密文件，请先解压再上传")
                        if info.file_size > MAX_FILE or total + info.file_size > MAX_BATCH:
                            raise ValueError("每份简历最多 20 MB，每批解压后最多 120 MB")
                        display = re.sub(r"[\x00-\x1f]", "", info.filename.replace("\\", "/").split("/")[-1])[:180]
                        entries.append((display, b"" if inspect_only else archive.read(info)))
                        total += info.file_size
                        if enforce_limit and len(entries) > MAX_FILES:
                            raise ValueError(f"每批最多 {MAX_FILES} 份简历，请分批上传")
            except zipfile.BadZipFile:
                raise ValueError("ZIP 文件损坏，请重新压缩或直接上传简历") from None
        else:
            if Path(filename).suffix.lower() not in SUPPORTED:
                raise ValueError(f"不支持的文件：{filename}。旧版 .doc 请先转换为 .docx 或 PDF")
            if not content or len(content) > MAX_FILE:
                raise ValueError(f"{filename} 为空或超过 20 MB")
            entries.append((filename, content))
            total += len(content)
        if total > MAX_BATCH or (enforce_limit and len(entries) > MAX_FILES):
            raise ValueError(f"每批最多 {MAX_FILES} 份简历，总大小最多 120 MB")
    if not entries:
        raise ValueError("请上传至少一份支持的简历文件")
    return entries


def create_app(data_root=None):
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.config.update(MAX_CONTENT_LENGTH=140 * 1024 * 1024, MAX_FORM_MEMORY_SIZE=1024 * 1024,
                      MAX_FORM_PARTS=120, JSON_AS_ASCII=False)
    app.json.ensure_ascii = False
    store = Store(data_root or default_data_root())
    store.recover()
    manager = Manager(store)
    token = secrets.token_urlsafe(32)
    app.extensions.update(resume_store=store, resume_manager=manager, app_token=token)

    @app.before_request
    def local_access():
        if request.host.split(":")[0] not in {"localhost", "127.0.0.1"}:
            return jsonify(error="此工具只接受本机访问"), 403
        if request.path.startswith("/api/"):
            provided = request.headers.get("X-App-Token", "")
            if not secrets.compare_digest(provided, token):
                return jsonify(error="页面会话已过期，请刷新页面"), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(Exception)
    def error_handler(error):
        if isinstance(error, HTTPException):
            message = "上传内容超过 140 MB，请减少文件数量" if error.code == 413 else "请求无效或页面不存在"
            return jsonify(error=message), error.code
        if isinstance(error, KeyError):
            return jsonify(error="批次或候选人不存在"), 404
        if isinstance(error, (ValueError, TypeError, json.JSONDecodeError)):
            return jsonify(error=str(error)[:500]), 400
        app.logger.error("Unhandled application error: %s", type(error).__name__)
        return jsonify(error="操作未完成，请稍后重试"), 500

    @app.get("/")
    def index():
        return render_template("index.html", app_token=token)

    @app.get("/health")
    def health():
        return jsonify(ok=True, application="resume-workbench", version="1.4.0")

    @app.get("/api/config")
    def config_defaults():
        return jsonify(base_url=os.getenv("RESUME_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                       model=os.getenv("RESUME_MODEL", "qwen-plus"), has_env_key=bool(os.getenv("RESUME_API_KEY")),
                       max_files=MAX_FILES, max_file_mb=20)

    @app.post("/api/test")
    def api_test():
        return jsonify(test_connection(validate_config(request.get_json() or {})))

    @app.post("/api/uploads/inspect")
    def inspect_uploads():
        entries = uploaded_files(enforce_limit=False, inspect_only=True)
        return jsonify(resume_count=len(entries), upload_count=len(request.files.getlist("files")),
                       max_files=MAX_FILES, within_limit=len(entries) <= MAX_FILES)

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jobs=store.list_jobs())

    @app.get("/api/talent")
    def talent_list():
        return jsonify(store.list_talent(request.args.get("q", ""), request.args.get("degree", ""),
                                        int(request.args.get("page", 1)), int(request.args.get("page_size", 25)),
                                        request.args.get("deleted") == "1"))

    @app.get("/api/talent/<talent_id>")
    def talent_detail(talent_id):
        return jsonify(store.get_talent(talent_id))

    @app.get("/api/talent/counts")
    def talent_counts():
        return jsonify(store.talent_counts())

    @app.get("/api/talent/template")
    def talent_template():
        data = normalize_candidate({"name": ""})
        return jsonify(data=data, derived=derived(data), raw_text="", remark="", short_remark="", remark_color="#18594c", can_edit=True)

    def talent_data(payload, raw_text=""):
        data = normalize_candidate(payload)
        data["evidence"] = [item for item in data["evidence"] if item["quote"] and item["quote"] in raw_text]
        for key in ("education", "experience", "projects", "qualifications"):
            for item in data[key]:
                if item["source_text"] and item["source_text"] not in raw_text:
                    item["source_text"] = ""
        return data

    @app.post("/api/talent")
    def talent_create():
        payload = request.get_json() or {}
        talent_id = store.create_talent(talent_data(payload), remark_values(payload, prefix="_"))
        return jsonify(id=talent_id), 201

    @app.put("/api/talent/<talent_id>")
    def talent_edit(talent_id):
        row = store.get_talent(talent_id)
        payload = request.get_json() or {}
        store.update_talent(talent_id, talent_data(payload, row["raw_text"]), **remark_values(payload, prefix="_"))
        return jsonify(ok=True)

    @app.put("/api/talent/<talent_id>/remark")
    def talent_remark(talent_id):
        values = remark_values(request.get_json() or {})
        if not values:
            raise ValueError("请提供需要保存的备注内容")
        store.update_talent(talent_id, **values)
        return jsonify(ok=True)

    @app.delete("/api/talent/<talent_id>")
    def talent_delete(talent_id):
        store.remove_talent(talent_id)
        return jsonify(ok=True, message="已移到回收站，可以恢复")

    @app.post("/api/talent/<talent_id>/restore")
    def talent_restore(talent_id):
        store.remove_talent(talent_id, restore=True)
        return jsonify(ok=True)

    @app.get("/api/talent/export/<format>")
    def talent_export(format):
        result = store.list_talent(request.args.get("q", ""), request.args.get("degree", ""), all_rows=True)
        job = {"id": "talent", "title": "候选人数据库（当前筛选结果）", "model": "本地人才库", "is_demo": 0,
               "rows": result["rows"], "stats": statistics(result["rows"])}
        if format == "xlsx":
            content, mime, name = export_excel(job), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "候选人数据库.xlsx"
        elif format == "pdf":
            content, mime, name = export_pdf(job), "application/pdf", "候选人数据库汇总.pdf"
        else:
            raise ValueError("不支持的导出格式")
        return send_file(io.BytesIO(content), mimetype=mime, as_attachment=True, download_name=name)

    @app.get("/api/jobs/<job_id>")
    def job_detail(job_id):
        return jsonify(store.get_job(job_id, full=False))

    @app.get("/api/jobs/<job_id>/rows/<row_id>")
    def candidate_detail(job_id, row_id):
        job = store.get_job(job_id)
        row = next((row for row in job["rows"] if row["id"] == row_id), None)
        if row is None:
            raise KeyError(row_id)
        return jsonify(row)

    @app.post("/api/jobs")
    def create_job():
        config = validate_config(json.loads(request.form.get("config", "{}")))
        entries = uploaded_files()
        job_id = uuid.uuid4().hex
        manager.reserve(job_id)
        folder = store.root / "uploads" / job_id
        try:
            folder.mkdir(parents=True)
            digests, rows = {}, []
            for filename, content in entries:
                row_id, digest = uuid.uuid4().hex, sha256(content).hexdigest()
                duplicate_of = digests.get(digest, "")
                path = folder / (row_id + Path(filename).suffix.lower())
                if not duplicate_of:
                    path.write_bytes(content)
                    digests[digest] = row_id
                rows.append({"id": row_id, "filename": filename, "path": str(path) if not duplicate_of else "", "digest": digest,
                             "status": "duplicate" if duplicate_of else "queued", "duplicate_of": duplicate_of})
            title = request.form.get("title", "").strip()[:80] or "简历批次 " + __import__("datetime").datetime.now().strftime("%m月%d日")
            store.create_job(job_id, title, config["model"], rows)
            manager.start(job_id, config)
        except Exception:
            # folder is a newly generated child of the application's uploads directory.
            if folder.exists() and folder.resolve().parent == (store.root / "uploads").resolve():
                shutil.rmtree(folder)
            manager.release()
            raise
        return jsonify(id=job_id), 202

    @app.post("/api/demo")
    def demo():
        job_id = uuid.uuid4().hex
        store.create_job(job_id, "中文简历整理示例", "演示数据 / 未调用 API", demo_rows(), demo=True)
        return jsonify(id=job_id)

    @app.put("/api/jobs/<job_id>/rows/<row_id>")
    def edit_candidate(job_id, row_id):
        job = store.get_job(job_id)
        if job["status"] != "completed":
            raise ValueError("请等待本批次处理完成后修改结果")
        row = next((r for r in job["rows"] if r["id"] == row_id), None)
        if row is None:
            raise KeyError(row_id)
        if row["status"] != "done":
            raise ValueError("只有成功解析的候选人可以编辑")
        payload = request.get_json() or {}
        candidate = normalize_candidate(payload)
        values = {"data": candidate}
        values.update(remark_values(payload, prefix="_"))
        # Evidence quotes remain tied to the stored original text, including after edits.
        candidate["evidence"] = [e for e in candidate["evidence"] if e["quote"] and e["quote"] in row["raw_text"]]
        for item in [item for key in ("education", "experience", "projects", "qualifications") for item in candidate[key]]:
            if item["source_text"] and item["source_text"] not in row["raw_text"]:
                item["source_text"] = ""
        values["edited"] = int(bool(row["edited"] or candidate != row["data"]))
        store.update_row(row_id, **values)
        return jsonify(ok=True)

    @app.put("/api/jobs/<job_id>/rows/<row_id>/remark")
    def edit_remark(job_id, row_id):
        job = store.get_job(job_id, full=False)
        row = next((r for r in job["rows"] if r["id"] == row_id), None)
        if row is None:
            raise KeyError(row_id)
        if row["status"] != "done":
            raise ValueError("请在候选人解析成功后添加备注")
        payload = request.get_json() or {}
        values = remark_values(payload)
        if not values:
            raise ValueError("请提供需要保存的备注内容")
        store.update_row(row_id, **values)
        row.update(values)
        return jsonify(ok=True, **{key:row[key] for key in ("remark", "short_remark", "remark_color")})

    @app.post("/api/jobs/<job_id>/retry")
    def retry(job_id):
        job = store.get_job(job_id)
        if job["status"] != "completed" or job["is_demo"]:
            raise ValueError("本批次当前无法重试")
        config = validate_config(request.get_json() or {})
        rows = [r for r in store.source_rows(job_id) if r["status"] in {"failed", "canceled"} and r["path"]]
        if not rows:
            raise ValueError("没有需要重试的文件")
        manager.reserve(job_id)
        try:
            for row in rows:
                store.update_row(row["id"], status="queued", error="")
            store.set_status(job_id, "running")
            manager.start(job_id, config, {r["id"] for r in rows})
        except Exception:
            manager.release()
            raise
        return jsonify(ok=True), 202

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        if manager.active != job_id:
            raise ValueError("本批次没有正在进行的任务")
        manager.cancel.set()
        store.set_status(job_id, "canceling")
        return jsonify(ok=True, message="正在停止；已经发出的模型请求将完成，等待中的文件会停止")

    @app.post("/api/jobs/<job_id>/education")
    @app.post("/api/jobs/<job_id>/supplement")
    def update_education(job_id):
        job = store.get_job(job_id)
        if job["status"] != "completed" or job["is_demo"]:
            raise ValueError("请在真实简历批次完成后补全信息")
        config = validate_config(request.get_json() or {})
        rows = [row for row in store.source_rows(job_id) if row["status"] == "done"]
        if not rows:
            raise ValueError("没有成功解析的简历可以更新信息")
        manager.reserve(job_id)
        try:
            for row in rows:
                store.update_row(row["id"], status="queued", error="")
            store.set_status(job_id, "running")
            manager.start(job_id, config, {r["id"] for r in rows}, education_only="supplement" if request.path.endswith("/supplement") else True)
        except Exception:
            manager.release()
            raise
        return jsonify(ok=True), 202

    @app.delete("/api/jobs/<job_id>")
    def delete(job_id):
        job = store.get_job(job_id)
        if job["status"] != "completed":
            raise ValueError("请先停止并等待本批次完成后再删除")
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("无效批次编号")
        folder = store.root / "uploads" / job_id
        if folder.exists() and folder.resolve().parent == (store.root / "uploads").resolve():
            shutil.rmtree(folder)
        store.delete_job(job_id)
        return jsonify(ok=True)

    @app.get("/api/jobs/<job_id>/export/<format>")
    def export(job_id, format):
        job = store.get_job(job_id)
        if job["status"] != "completed":
            raise ValueError("请等待处理完成或停止批次后导出")
        if format == "xlsx":
            content, mime, name = export_excel(job), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "简历整理.xlsx"
        elif format == "pdf":
            content, mime, name = export_pdf(job), "application/pdf", "简历汇总报告.pdf"
        elif format == "json":
            content, mime, name = json.dumps(job, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", "简历整理.json"
        else:
            raise ValueError("不支持的导出格式")
        return send_file(io.BytesIO(content), mimetype=mime, as_attachment=True, download_name=name)

    return app


if __name__ == "__main__":
    import argparse
    import webbrowser
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    application = create_app()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1, lambda: webbrowser.open(url)).start()
    print(f"Resume Workbench: {url}")
    application.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)
