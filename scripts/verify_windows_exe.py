"""Test the shipped EXE alone, in an empty directory with a clean Python environment."""
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer

import requests
from pypdf import PdfReader
from docx import Document

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_workbench import ModelHandler


def unused_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


directory = Path(tempfile.mkdtemp(prefix="独立电脑验证_", dir=ROOT / ".tmp"))
executable = directory / "简历工作台.exe"
shutil.copy2(ROOT / "output" / "windows" / "简历工作台.exe", executable)
environment = os.environ.copy()
for key in ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "RESUME_API_KEY", "RESUME_BASE_URL", "RESUME_MODEL", "RESUME_DATA_DIR"]:
    environment.pop(key, None)
system_root = environment.get("SYSTEMROOT", r"C:\Windows")
environment["PATH"] = str(Path(system_root) / "System32") + ";" + system_root
runtime = directory / "临时运行库"
runtime.mkdir()
environment["TEMP"] = environment["TMP"] = str(runtime)
port = unused_port()
url = f"http://127.0.0.1:{port}"
server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
process = subprocess.Popen([str(executable), "--port", str(port), "--no-browser", "--no-tray"],
                           cwd=directory, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    started = time.monotonic()
    while time.monotonic() - started < 45:
        if process.poll() is not None:
            raise RuntimeError("Packaged executable exited before serving the UI")
        try:
            health = requests.get(url + "/health", timeout=1).json()
            if health.get("version") == "1.4.0":
                break
        except requests.RequestException:
            time.sleep(.2)
    else:
        raise RuntimeError("Packaged executable startup timed out")
    print("Standalone EXE startup passed", flush=True)
    page = requests.get(url + "/", timeout=5)
    assert page.status_code == 200 and "各阶段院校与专业" in page.text
    assert 'mailto:antirezk@gmail.com' in page.text and '开发者 Kyle' in page.text
    assert 'id="concurrency"' not in page.text
    assert 'id="openDatabase"' in page.text
    token = re.search(r'name="app-token" content="([^"]+)"', page.text).group(1)
    headers = {"X-App-Token": token}
    counted = requests.post(url + '/api/uploads/inspect', headers=headers,
                            files=[('files', ('自动计数.txt', '姓名：测试'.encode()))], timeout=5).json()
    assert counted['resume_count'] == 1 and counted['within_limit']
    assert requests.get(url + "/static/education.css", timeout=5).status_code == 200
    config = requests.get(url + "/api/config", headers=headers, timeout=5).json()
    assert config["max_files"] == 50
    config = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "local-test", "api_key": "exe-test-secret", "concurrency": 3}
    demo_id = requests.post(url + "/api/demo", json={}, headers=headers, timeout=5).json()["id"]
    demo_job = requests.get(url + f"/api/jobs/{demo_id}", headers=headers, timeout=5).json()
    assert len(demo_job["rows"][3]["data"]["education"]) == 3
    assert requests.get(url+'/api/talent/counts',headers=headers,timeout=5).json()['total'] == 0
    assert demo_job["rows"][1]["derived"]["internship_months"] == 6
    assert len(demo_job["rows"][1]["data"]["experience"]) == 3
    remark_row = demo_job['rows'][1]
    result = requests.put(url + f"/api/jobs/{demo_id}/rows/{remark_row['id']}/remark", json={'remark':'已沟通，周五面试。\n等待确认到岗时间。','short_remark':'待确认到岗','remark_color':'#285d9b'}, headers=headers, timeout=5)
    assert result.status_code == 200
    updated = requests.get(url + f"/api/jobs/{demo_id}", headers=headers, timeout=5).json()['rows'][1]
    assert updated['remark'] == '已沟通，周五面试。\n等待确认到岗时间。' and updated['edited'] == 0
    assert updated['short_remark'] == '待确认到岗' and updated['remark_color'] == '#285d9b'
    result = requests.put(url + f"/api/jobs/{demo_id}/rows/{remark_row['id']}", json={**updated['data'], '_remark':updated['remark']}, headers=headers, timeout=5)
    assert result.status_code == 200
    updated = requests.get(url + f"/api/jobs/{demo_id}", headers=headers, timeout=5).json()['rows'][1]
    assert updated['edited'] == 0 and ModelHandler.calls == 0
    reports = ROOT / "output" / "verification" / "exe"
    reports.mkdir(parents=True, exist_ok=True)
    for format in ["xlsx", "pdf"]:
        response = requests.get(url + f"/api/jobs/{demo_id}/export/{format}", headers=headers, timeout=30)
        assert response.status_code == 200, response.text[:500]
        (reports / ("EXE教育明细_演示." + format)).write_bytes(response.content)
    text = "\n".join(p.extract_text() for p in PdfReader(reports / "EXE教育明细_演示.pdf").pages)
    for value in ["四川大学", "浙江大学", "分布式系统", "完整教育经历", "本科", "硕士", "博士", "完整工作经历", "完整实习经历", "初级后端工程师", "知行科技", "6个月", "大学英语六级", "已沟通，周五面试。", "等待确认到岗时间。", "待确认到岗"]:
        assert value in text, value
    print("Packaged Chinese font, PDF and Excel exports passed", flush=True)
    doc = Document()
    doc.add_paragraph("姓名：Word测试候选人\n某大学 本科 计算机科学\n2020-01至2022-12 某公司 软件工程师\n负责业务接口开发与数据分析 Python SQL")
    word = io.BytesIO()
    doc.save(word)
    files = [
        ("files", ("文字.pdf", (ROOT / "output/verification/测试简历_文字.pdf").read_bytes())),
        ("files", ("扫描.pdf", (ROOT / "output/verification/测试简历_扫描.pdf").read_bytes())),
        ("files", ("图片.png", (ROOT / "output/verification/测试简历_扫描.png").read_bytes())),
        ("files", ("Word.docx", word.getvalue())),
    ]
    for i in range(46):
        files.append(("files", (f"简历{i}.txt", f"姓名：候选人{i}\n某大学 本科 计算机科学\n2020-01至2022-12 某公司 软件工程师\n负责业务接口开发与数据分析 Python SQL".encode())))
    result = requests.post(url + "/api/jobs", headers=headers, data={"config": json.dumps(config), "title": "EXE 50 份混合格式验证"}, files=files, timeout=10)
    assert result.status_code == 202, result.text
    job_id = result.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = requests.get(url + f"/api/jobs/{job_id}", headers=headers, timeout=5).json()
        if job["status"] == "completed":
            break
        time.sleep(.3)
    failures = [(row["filename"], row["error"]) for row in job["rows"] if row["status"] != "done"]
    assert job["stats"]["done"] == 50, failures
    assert "OCR" in job["rows"][1]["method"]
    assert job["rows"][2]["method"] == "OCR"
    assert ModelHandler.calls == 50, ModelHandler.calls
    library = requests.get(url+'/api/talent',headers=headers,timeout=10).json()
    assert library['total'] == 50 and library['pages'] == 2
    target = library['rows'][0]
    talent_path = url + '/api/talent/' + target['id']
    updated_data = {**target['data'],'location':'数据库编辑验证','_short_remark':'待联系','_remark':'数据库维护备注','_remark_color':'#285d9b'}
    assert requests.put(talent_path,json=updated_data,headers=headers,timeout=5).status_code == 200
    assert requests.get(talent_path,headers=headers,timeout=5).json()['remark'] == '数据库维护备注'
    assert requests.delete(talent_path,headers=headers,timeout=5).status_code == 200
    assert requests.get(url+'/api/talent/counts',headers=headers,timeout=5).json()['total'] == 49
    assert requests.post(talent_path+'/restore',headers=headers,timeout=5).status_code == 200
    manual = requests.post(url+'/api/talent',json={'name':'手动数据库验证','_remark':'人工录入'},headers=headers,timeout=5)
    assert manual.status_code == 201
    assert requests.get(url+'/api/talent/counts',headers=headers,timeout=5).json()['total'] == 51
    for format in ('xlsx','pdf'):
        result = requests.get(url+'/api/talent/export/'+format,params={'q':'手动数据库验证'},headers=headers,timeout=30)
        assert result.status_code == 200
    assert ModelHandler.calls == 50
    print('Packaged cumulative local database CRUD, recycle and export passed',flush=True)
    db = directory / "简历工作台数据" / "resumes.sqlite3"
    assert db.exists() and b"exe-test-secret" not in db.read_bytes()
    print("Packaged offline OCR, DOCX and 50-file API batch passed", flush=True)
    (reports / "验证结果.json").write_text(json.dumps({"standalone_exe": True, "python_env_removed": True,
        "max_files": 50, "batch_completed": 50, "offline_ocr": True, "docx": True,
        "version": "1.4.0", "cumulative_local_database": True, "database_crud": True, "database_export": True, "short_and_detailed_remarks": True, "remark_color": True, "candidate_remarks": True, "automatic_count": True, "developer_contact": True, "chinese_pdf": True, "excel": True, "all_education_levels": True,
        "separate_work_and_internship": True, "per_role_duration": True, "supplemental_qualifications": True,
        "persistent_data_outside_unpack_dir": True}, ensure_ascii=False, indent=2), encoding="utf-8")
finally:
    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    process.wait(timeout=10)
    server.shutdown()
    server.server_close()
    # Keep isolated fixtures for inspection; they contain synthetic resumes only.
    print("Verification directory:", directory, flush=True)
