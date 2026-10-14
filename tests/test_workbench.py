import copy
import io
import json
import re
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

from app import create_app
from resume_tool.demo import demo_rows
from resume_tool.schema import derived, normalize_candidate, highest_degree, education_summary, duration
from resume_tool.store import Store
from resume_tool.llm import validate_config


class ModelHandler(BaseHTTPRequestHandler):
    calls = 0
    fail_marker = True
    slow = False
    bad_json_once = False
    lock = threading.Lock()

    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.lock:
            type(self).calls += 1
        prompt = request["messages"][1]["content"]
        if self.slow:
            time.sleep(.15)
        if self.fail_marker and "SIMULATED_FAILURE" in prompt:
            self.send_response(401)
            self.end_headers()
            return
        name = re.search(r"姓名：([^\n]+)", prompt)
        data = {"name": name.group(1) if name else "测试候选人", "phone": "0013800000000", "email": "",
                "location": "上海", "target_role": "数据分析师", "summary": "负责数据清洗与分析。", "skills": ["Python", "SQL", "Python"],
                "education": [{"school": "测试大学", "degree": "本科", "major": "统计学", "start_date": "2015-09", "end_date": "2019-06"}],
                "experience": [{"company": "测试公司", "title": "数据分析师", "start_date": "2020-01", "end_date": "2021-12", "description": "负责数据清洗与分析。", "is_internship": False}],
                "projects": [], "evidence": [{"field": "name", "quote": name.group(1) if name else "测试候选人"}, {"field": "location", "quote": "虚构原文片段"}], "notes": []}
        content = json.dumps(data, ensure_ascii=False)
        if self.bad_json_once:
            type(self).bad_json_once = False
            content = "this is invalid json"
        response = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class SchemaTests(unittest.TestCase):
    def candidate(self, experience):
        return normalize_candidate({"name": "测试", "experience": experience})

    def test_overlap_and_internship(self):
        data = self.candidate([
            {"company": "A", "start_date": "2020-01", "end_date": "2020-12"},
            {"company": "B", "start_date": "2020-06", "end_date": "2021-02"},
            {"company": "C", "start_date": "2018-01", "end_date": "2019-12", "is_internship": True}])
        self.assertEqual(derived(data, date(2026, 10, 13))["experience_years"], 1.2)

    def test_missing_dates_are_not_zero(self):
        info = derived(self.candidate([{"company": "A", "start_date": "2020", "end_date": "至今"}]))
        self.assertIsNone(info["experience_years"])
        self.assertTrue(info["years_partial"])
        self.assertTrue(info["warnings"])

    def test_separate_totals_overlap_unknown_and_per_role_duration(self):
        data = self.candidate([
            {"company": "A", "title": "开发", "employment_type": "工作", "start_date": "2020-01", "end_date": "2020-12"},
            {"company": "A", "title": "高级开发", "employment_type": "工作", "start_date": "2020-07", "end_date": "2021-06"},
            {"company": "B", "title": "实习生", "employment_type": "实习", "start_date": "2019-01", "end_date": "2019-06"},
            {"company": "C", "title": "实习生", "employment_type": "实习", "start_date": "2019-04", "end_date": "2019-09"},
            {"company": "D", "employment_type": "兼职", "start_date": "2018-01", "end_date": "2022-12"},
            {"company": "E", "employment_type": "未明确", "start_date": "2018", "end_date": "至今"}])
        info = derived(data, date(2026, 10, 14))
        self.assertEqual(info["experience_months"], 18)
        self.assertEqual(info["internship_months"], 9)
        self.assertEqual([d["months"] for d in info["experience_details"]], [12,12,6,6,60,None])
        self.assertTrue(info["years_partial"] and info["internship_partial"])
        self.assertTrue(any("重叠" in note for note in info["warnings"]))
        self.assertTrue(data["experience"][2]["is_internship"])

    def test_invalid_dates_and_current_month_are_grounded(self):
        for start, end in [("2020-02-30", "2020-12"), ("2021-01", "2020-12"), ("2020", "2020-12"), ("2026-11", "2026-12"), ("2026-10-20", "至今"), ("2020-02-20", "2020-02-10"), ("2026-10-01", "2026-10-20")]:
            self.assertIsNone(duration({"start_date":start,"end_date":end}, date(2026,10,14))["months"])
        self.assertEqual(duration({"start_date":"2026-10","end_date":"至今"}, date(2026,10,14))["months"],1)

    def test_supplemental_facts_survive_normalization(self):
        data = normalize_candidate({"name":"测试","target_city":"杭州","availability":"两周","languages":[{"name":"英语","test_score":"IELTS 7"}],"qualifications":[{"category":"证书","name":"PMP","issuer":"PMI"}],"projects":[{"name":"项目","role":"后端开发","achievements":"耗时降低30%"}],"links":[{"label":"作品集","url":"https://example.invalid"}]})
        self.assertEqual(data["projects"][0]["role"],"后端开发")
        self.assertEqual(data["languages"][0]["test_score"],"IELTS 7")

    def test_types_and_skills(self):
        with self.assertRaises(ValueError):
            normalize_candidate({"name": "测试", "experience": [{"company": "A", "is_internship": "false"}]})
        self.assertEqual(normalize_candidate({"name": "测试", "skills": ["Python", "python", "SQL"]})["skills"], ["Python", "SQL"])

    def test_config_does_not_allow_credentials_or_remote_http(self):
        for url in ["http://example.com/v1", "https://key@example.com/v1", "https://example.com/v1?api_key=abc"]:
            with self.assertRaises(ValueError):
                validate_config({"base_url": url, "model": "test", "api_key": "secret"})

    def test_education_keeps_separate_levels_schools_and_majors(self):
        candidate = normalize_candidate({"name": "教育测试", "education": [
            {"school": "本科院校", "degree": "工学学士", "major": "软件工程", "college": "计算机学院"},
            {"school": "硕士院校", "degree": "工程硕士", "major": "计算机技术", "research_direction": "分布式系统"},
            {"school": "博士院校", "degree": "博士研究生", "major": "人工智能", "graduation_status": "在读", "academic_details": "获得研究生奖学金"}]})
        self.assertEqual([r["level"] for r in candidate["education"]], ["本科", "硕士", "博士"])
        self.assertEqual(highest_degree(candidate), "博士")
        self.assertEqual(candidate["education"][1]["research_direction"], "分布式系统")
        self.assertEqual(candidate["education"][2]["graduation_status"], "在读")
        self.assertIn("本科院校 / 软件工程", education_summary(candidate, "本科"))
        self.assertNotIn("硕士院校", education_summary(candidate, "本科"))

    def test_ambiguous_postgraduate_is_not_invented_as_masters(self):
        candidate = normalize_candidate({"name": "测试", "education": [{"school": "某大学", "degree": "研究生"}]})
        self.assertEqual(highest_degree(candidate), "研究生（层次未明确）")
        self.assertEqual(candidate["education"][0]["major"], "")
        self.assertTrue(any("专业" in warning for warning in derived(candidate)["warnings"]))
        self.assertEqual(normalize_candidate({"name": "测试", "education": [{}]})["education"], [])


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = create_app(self.folder.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.headers = {"X-App-Token": self.app.extensions["app_token"]}
        ModelHandler.calls, ModelHandler.fail_marker, ModelHandler.slow, ModelHandler.bad_json_once = 0, True, False, False
        self.config = {"base_url": f"http://127.0.0.1:{self.server.server_port}/v1", "model": "local-test", "api_key": "test-secret-never-persist", "concurrency": 3}

    def tearDown(self):
        active = self.app.extensions["resume_manager"]
        active.cancel.set()
        deadline = time.time() + 10
        while active.active and time.time() < deadline:
            time.sleep(.03)
        self.folder.cleanup()

    def upload(self, files):
        result = self.client.post("/api/jobs", data={"config": json.dumps(self.config), "title": "50份中文简历验证", "files": files}, headers=self.headers)
        self.assertEqual(result.status_code, 202, result.json)
        return result.json["id"]

    def wait(self, job_id):
        deadline = time.time() + 15
        while time.time() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}", headers=self.headers).json
            if job["status"] == "completed":
                return job
            time.sleep(.03)
        self.fail("Batch processing did not complete")

    def resume(self, name, extra=""):
        return (io.BytesIO(f"姓名：{name}\n测试大学 本科 统计学\n2020-01 至 2021-12 测试公司 数据分析师\n负责数据清洗与分析，技能 Python SQL。\n{extra}".encode("utf-8")), name + ".txt")

    def test_50_resume_batch_and_exports(self):
        job_id = self.upload([self.resume(f"候选人{i:02d}") for i in range(50)])
        job = self.wait(job_id)
        self.assertEqual(job["stats"]["done"], 50)
        self.assertEqual(ModelHandler.calls, 50)
        self.assertEqual(job["tokens"], 7500)
        self.assertEqual(job["rows"][0]["data"]["skills"], ["Python", "SQL"])
        self.assertEqual(len(job["rows"][0]["data"]["evidence"]), 1)
        excel = self.client.get(f"/api/jobs/{job_id}/export/xlsx", headers=self.headers)
        self.assertEqual(excel.status_code, 200)
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(io.BytesIO(excel.data)) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(len(sheet.findall("x:sheetData/x:row", ns)), 51)
            self.assertEqual(sheet.find(".//x:c[@r='B2']/x:is/x:t", ns).text, "0013800000000")
            self.assertEqual(sheet.find(".//x:c[@r='G2']/x:v", ns).text, "2.0")
            self.assertEqual(len(ET.fromstring(archive.read("xl/workbook.xml")).findall("x:sheets/x:sheet", ns)), 11)
        pdf = self.client.get(f"/api/jobs/{job_id}/export/pdf", headers=self.headers)
        self.assertEqual(pdf.status_code, 200, pdf.data[:100])
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf.data))
        text = "".join(page.extract_text() for page in reader.pages)
        self.assertIn("候选人49", text)
        self.assertIn("中文简历汇总报告", text)
        self.assertGreater(len(reader.pages), 4)
        self.assertNotIn(b"test-secret-never-persist", Path(self.folder.name, "resumes.sqlite3").read_bytes())

    def test_duplicate_and_zip_path_handling(self):
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            archive.writestr("../候选人甲.txt", self.resume("甲")[0].getvalue())
            archive.writestr("目录/候选人副本.txt", self.resume("甲")[0].getvalue())
        zipped.seek(0)
        job = self.wait(self.upload([(zipped, "简历.zip")]))
        self.assertEqual(job["stats"]["done"], 1)
        self.assertEqual(job["stats"]["duplicates"], 1)
        self.assertEqual(ModelHandler.calls, 1)
        uploads = list(Path(self.folder.name, "uploads").glob("*/*"))
        self.assertEqual(len(uploads), 1)
        self.assertNotEqual(uploads[0].name, "候选人甲.txt")

    def test_51_resumes_are_rejected_before_processing(self):
        result = self.client.post("/api/jobs", data={"config": json.dumps(self.config),
                                  "files": [self.resume(f"超限{i}") for i in range(51)]}, headers=self.headers)
        self.assertEqual(result.status_code, 400)
        self.assertIn("最多 50 份", result.json["error"])
        self.assertEqual(ModelHandler.calls, 0)
        self.assertEqual(self.client.get("/api/jobs", headers=self.headers).json["jobs"], [])

    def test_automatic_count_for_mixed_zip_and_duplicates_does_not_start_job(self):
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            archive.writestr("目录/甲.txt", "姓名：甲")
            archive.writestr("目录/乙.pdf", b"synthetic pdf fixture")
            archive.writestr("副本/甲.txt", "姓名：甲")
            archive.writestr("说明.md", "unsupported attachment")
            archive.writestr("__MACOSX/隐藏.txt", "ignored")
            archive.writestr("空目录/", "")
        zipped.seek(0)
        response = self.client.post("/api/uploads/inspect", data={"files": [(zipped,"简历.zip"), self.resume("额外简历")]}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["resume_count"], 4)
        self.assertEqual(response.json["upload_count"], 2)
        self.assertTrue(response.json["within_limit"])
        self.assertEqual(ModelHandler.calls, 0)
        self.assertEqual(self.client.get("/api/jobs", headers=self.headers).json["jobs"], [])

    def test_count_reports_51_and_rejects_invalid_archive(self):
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped,"w") as archive:
            for i in range(51):
                archive.writestr(f"简历{i}.txt", f"姓名：候选人{i}")
        zipped.seek(0)
        result = self.client.post("/api/uploads/inspect", data={"files":[(zipped,"超限.zip")]}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["resume_count"], 51)
        self.assertFalse(result.json["within_limit"])
        for content in (b"not a zip",):
            invalid = self.client.post("/api/uploads/inspect", data={"files":[(io.BytesIO(content),"损坏.zip")]}, headers=self.headers)
            self.assertEqual(invalid.status_code, 400)
        self.assertEqual(ModelHandler.calls, 0)

    def test_feedback_link_and_automatic_processing_ui(self):
        page = self.client.get("/").text
        self.assertIn('href="mailto:antirezk@gmail.com"', page)
        self.assertIn("开发者 Kyle",page)
        self.assertIn("自动识别简历数量",page)
        self.assertNotIn('id="concurrency"',page)

    def test_zip_and_mixed_uploads_share_50_resume_limit(self):
        for zipped_count, loose_count in [(51, 0), (50, 1), (49, 2)]:
            with self.subTest(zipped=zipped_count, loose=loose_count):
                zipped = io.BytesIO()
                with zipfile.ZipFile(zipped, "w") as archive:
                    for i in range(zipped_count):
                        archive.writestr(f"目录/候选人{i}.txt", self.resume(f"候选人{i}")[0].getvalue())
                zipped.seek(0)
                files = [(zipped, "简历.zip")] + [self.resume(f"额外{i}") for i in range(loose_count)]
                result = self.client.post("/api/jobs", data={"config": json.dumps(self.config), "files": files}, headers=self.headers)
                self.assertEqual(result.status_code, 400)
                self.assertIn("最多 50 份", result.json["error"])
                self.assertEqual(ModelHandler.calls, 0)
        config = self.client.get("/api/config", headers=self.headers).json
        self.assertEqual(config["max_files"], 50)

    def test_failed_retry_does_not_reparse_success_and_edit_persists(self):
        job_id = self.upload([self.resume("正常候选人"), self.resume("失败候选人", "SIMULATED_FAILURE")])
        job = self.wait(job_id)
        self.assertEqual((job["stats"]["done"], job["stats"]["failed"]), (1, 1))
        self.assertEqual(ModelHandler.calls, 2)
        ModelHandler.fail_marker = False
        response = self.client.post(f"/api/jobs/{job_id}/retry", json=self.config, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        job = self.wait(job_id)
        self.assertEqual(job["stats"]["done"], 2)
        self.assertEqual(ModelHandler.calls, 3)
        row = job["rows"][0]
        edited = copy.deepcopy(row["data"])
        edited["name"] = "已核对姓名"
        response = self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}", json=edited, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.wait(job_id)["rows"][0]["data"]["name"], "已核对姓名")
        self.assertEqual(ModelHandler.calls, 3)
        new_store = Store(self.folder.name)
        self.assertEqual(new_store.get_job(job_id)["rows"][0]["data"]["name"], "已核对姓名")

    def test_cancel_preserves_completed_results(self):
        ModelHandler.slow = True
        job_id = self.upload([self.resume(f"测试{i}") for i in range(12)])
        time.sleep(.05)
        response = self.client.post(f"/api/jobs/{job_id}/cancel", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        job = self.wait(job_id)
        self.assertEqual(len(job["rows"]), 12)
        self.assertTrue(any(r["status"] == "canceled" for r in job["rows"]))
        self.assertTrue(all(r["status"] in {"done", "canceled"} for r in job["rows"]))
        self.assertLess(ModelHandler.calls, 12)

    def test_json_repair_and_invalid_pdf_is_reported(self):
        ModelHandler.bad_json_once = True
        job_id = self.upload([self.resume("格式修复测试"), (io.BytesIO(b"not a pdf"), "损坏.pdf")])
        job = self.wait(job_id)
        self.assertEqual((job["stats"]["done"], job["stats"]["failed"]), (1, 1))
        self.assertEqual(ModelHandler.calls, 2)
        self.assertTrue(job["rows"][1]["error"])

    def test_local_access_and_demo(self):
        self.assertEqual(self.client.get("/api/jobs").status_code, 403)
        self.assertEqual(self.client.get("/api/jobs", headers={**self.headers, "Host": "evil.example"}).status_code, 403)
        demo = self.client.post("/api/demo", json={}, headers=self.headers)
        job = self.wait(demo.json["id"])
        self.assertEqual(job["stats"]["done"], 8)
        self.assertEqual(job["is_demo"], 1)
        self.assertEqual(ModelHandler.calls, 0)

    def test_education_refresh_preserves_manual_edits_and_original_on_failure(self):
        ModelHandler.fail_marker = False
        job_id = self.upload([self.resume("教育升级测试", "SIMULATED_FAILURE")])
        job = self.wait(job_id)
        row = job["rows"][0]
        edited = copy.deepcopy(row["data"])
        edited.update(phone="0099999", location="已核对城市")
        edited["_remark"] = "教育更新期间保留的面试备注"
        self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}", json=edited, headers=self.headers)
        response = self.client.post(f"/api/jobs/{job_id}/education", json=self.config, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        updated = self.wait(job_id)["rows"][0]
        self.assertEqual(updated["data"]["phone"], "0099999")
        self.assertEqual(updated["data"]["location"], "已核对城市")
        self.assertEqual(updated["remark"], "教育更新期间保留的面试备注")
        self.assertEqual(updated["data"]["education"][0]["level"], "本科")
        self.assertEqual(ModelHandler.calls, 2)
        previous_education = copy.deepcopy(updated["data"]["education"])
        ModelHandler.fail_marker = True
        self.client.post(f"/api/jobs/{job_id}/education", json=self.config, headers=self.headers)
        failed_refresh = self.wait(job_id)["rows"][0]
        self.assertEqual(failed_refresh["status"], "done")
        self.assertEqual(failed_refresh["data"]["education"], previous_education)
        self.assertEqual(failed_refresh["remark"], "教育更新期间保留的面试备注")
        self.assertTrue(any("教育信息更新未完成" in n for n in failed_refresh["data"]["notes"]))

    def test_detailed_education_is_present_in_both_exports(self):
        demo = self.client.post("/api/demo", json={}, headers=self.headers).json
        job_id = demo["id"]
        excel = self.client.get(f"/api/jobs/{job_id}/export/xlsx", headers=self.headers)
        with zipfile.ZipFile(io.BytesIO(excel.data)) as archive:
            overview = archive.read("xl/worksheets/sheet1.xml").decode()
            education = archive.read("xl/worksheets/sheet3.xml").decode()
            self.assertIn("本科院校", overview)
            self.assertIn("硕士专业", overview)
            self.assertIn("博士就读时间", overview)
            self.assertIn("实习累计月数", overview)
            internships = archive.read("xl/worksheets/sheet7.xml").decode()
            self.assertIn("知行科技", internships)
            self.assertIn("实习生", internships)
            self.assertIn("制作6份数据分析周报", internships)
            work = archive.read("xl/worksheets/sheet2.xml").decode()
            self.assertIn("初级后端工程师", work)
            self.assertNotIn("知行科技", work)
            for value in ("四川大学", "浙江大学", "软件工程", "计算机技术", "数学与应用数学", "统计学习", "博士论文研究高维统计模型"):
                self.assertIn(value, education)
        pdf = self.client.get(f"/api/jobs/{job_id}/export/pdf", headers=self.headers)
        from pypdf import PdfReader
        text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.data)).pages)
        for value in ("完整教育经历", "四川大学", "浙江大学", "本科", "硕士", "博士", "分布式系统", "统计学习", "完整工作经历", "完整实习经历", "知行科技", "初级后端工程师", "6个月", "大学英语六级"):
            self.assertIn(value, text)

    def test_supplement_refresh_preserves_edited_experience_and_contacts(self):
        ModelHandler.fail_marker = False
        job_id = self.upload([self.resume("补全测试", "SIMULATED_FAILURE")])
        row = self.wait(job_id)["rows"][0]
        edited = copy.deepcopy(row["data"])
        edited["phone"] = "0099999"
        edited["experience"][0]["description"] = "人工核对的职责"
        edited["experience"][0]["achievements"] = "人工核对的成果"
        edited["_remark"] = "补全期间保留的沟通备注"
        self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}", json=edited, headers=self.headers)
        response = self.client.post(f"/api/jobs/{job_id}/supplement", json=self.config, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        refreshed = self.wait(job_id)["rows"][0]
        self.assertEqual(refreshed["remark"], "补全期间保留的沟通备注")
        updated = refreshed["data"]
        self.assertEqual(updated["phone"], "0099999")
        self.assertEqual(updated["experience"][0]["description"], "人工核对的职责")
        self.assertEqual(updated["experience"][0]["achievements"], "人工核对的成果")
        ModelHandler.fail_marker = True
        self.client.post(f"/api/jobs/{job_id}/supplement", json=self.config, headers=self.headers)
        failed = self.wait(job_id)["rows"][0]
        self.assertEqual(failed["status"], "done")
        self.assertEqual(failed["data"]["experience"], updated["experience"])
        self.assertEqual(failed["remark"], "补全期间保留的沟通备注")
        self.assertTrue(any("经历信息补全未完成" in n for n in failed["data"]["notes"]))

    def test_per_candidate_remark_edit_clear_persist_and_exports(self):
        demo = self.client.post("/api/demo", json={}, headers=self.headers).json
        job_id = demo["id"]
        original = self.wait(job_id)
        row = original["rows"][0]
        remark = '=跟进记录\n已电话沟通，周五14:00面试。\n候选人提问：<时间&地点>，待确认。'
        path = f"/api/jobs/{job_id}/rows/{row['id']}/remark"
        response = self.client.put(path, json={"remark":remark}, headers=self.headers)
        self.assertEqual(response.status_code,200)
        saved = Store(self.folder.name).get_job(job_id)
        self.assertEqual(saved["rows"][0]["remark"],remark)
        self.assertEqual(saved["rows"][1]["remark"],original["rows"][1]["remark"])
        self.assertEqual(saved["rows"][0]["data"],row["data"])
        self.assertEqual(saved["rows"][0]["edited"],0)
        self.assertEqual(ModelHandler.calls,0)
        # Older clients editing the resume without a remark field preserve it.
        self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}", json=row["data"], headers=self.headers)
        self.assertEqual(self.wait(job_id)["rows"][0]["remark"],remark)
        self.assertEqual(self.wait(job_id)["rows"][0]["edited"],0)
        self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}", json={**row['data'],'_remark':remark}, headers=self.headers)
        self.assertEqual(self.wait(job_id)["rows"][0]["edited"],0)
        excel = self.client.get(f"/api/jobs/{job_id}/export/xlsx",headers=self.headers)
        ns = {"x":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(io.BytesIO(excel.data)) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            cell = sheet.find(".//x:c[@r='N2']",ns)
            self.assertEqual(cell.attrib['t'],'inlineStr')
            self.assertEqual(cell.find('x:is/x:t',ns).text,remark)
        pdf = self.client.get(f"/api/jobs/{job_id}/export/pdf",headers=self.headers)
        from pypdf import PdfReader
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.data)).pages)
        self.assertIn('已电话沟通，周五14:00面试。',text)
        self.assertIn('详细备注（人工填写）',text)
        self.client.put(path,json={"remark":"无"},headers=self.headers)
        self.assertEqual(self.wait(job_id)["rows"][0]["remark"],"无")
        self.client.put(path,json={"remark":""},headers=self.headers)
        self.assertEqual(self.wait(job_id)["rows"][0]["remark"],"")

    def test_invalid_remark_does_not_change_resume_or_cross_candidate(self):
        job_id = self.client.post('/api/demo',json={},headers=self.headers).json['id']
        row = self.wait(job_id)['rows'][0]
        for invalid in (None,12,[],"x"*5001):
            response = self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}/remark",json={"remark":invalid},headers=self.headers)
            self.assertEqual(response.status_code,400)
        payload = {**row['data'],'name':'不应保存','_remark':'x'*5001}
        self.assertEqual(self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}",json=payload,headers=self.headers).status_code,400)
        self.assertEqual(self.wait(job_id)['rows'][0]['data']['name'],row['data']['name'])
        other_job = self.client.post('/api/demo',json={},headers=self.headers).json['id']
        result = self.client.put(f"/api/jobs/{other_job}/rows/{row['id']}/remark",json={"remark":"越界备注"},headers=self.headers)
        self.assertEqual(result.status_code,404)

    def test_short_and_detailed_remarks_with_color_persist_export_and_partial_edit(self):
        job_id = self.client.post('/api/demo',json={},headers=self.headers).json['id']
        row = self.wait(job_id)['rows'][0]
        path = f"/api/jobs/{job_id}/rows/{row['id']}/remark"
        response = self.client.put(path,json={'short_remark':'不符合','remark_color':'#C04030','remark':'详细沟通记录：工作地点需要再确认。'},headers=self.headers)
        self.assertEqual(response.status_code,200)
        stored = Store(self.folder.name).get_job(job_id)['rows'][0]
        self.assertEqual((stored['short_remark'],stored['remark_color'],stored['remark']),('不符合','#c04030','详细沟通记录：工作地点需要再确认。'))
        self.assertEqual(stored['edited'],0)
        self.assertEqual(stored['data'],row['data'])
        self.assertEqual(ModelHandler.calls,0)
        ns = {'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        xlsx = self.client.get(f'/api/jobs/{job_id}/export/xlsx',headers=self.headers)
        with zipfile.ZipFile(io.BytesIO(xlsx.data)) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            cell = sheet.find(".//x:c[@r='O2']",ns)
            self.assertEqual(cell.find('x:is/x:t',ns).text,'不符合')
            styles = ET.fromstring(archive.read('xl/styles.xml'))
            xf = styles.find('x:cellXfs',ns)[int(cell.attrib['s'])]
            font = styles.find('x:fonts',ns)[int(xf.attrib['fontId'])]
            self.assertEqual(font.find('x:color',ns).attrib['rgb'],'FFC04030')
        pdf = self.client.get(f'/api/jobs/{job_id}/export/pdf',headers=self.headers)
        from pypdf import PdfReader
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.data)).pages)
        self.assertIn('不符合',text)
        self.assertIn('详细沟通记录：工作地点需要再确认。',text)
        self.client.put(path,json={'remark':'只改详细内容'},headers=self.headers)
        stored = self.wait(job_id)['rows'][0]
        self.assertEqual((stored['short_remark'],stored['remark_color']),('不符合','#c04030'))
        self.client.put(f"/api/jobs/{job_id}/rows/{row['id']}",json={**stored['data'],'_short_remark':'待联系','_remark_color':'#285d9b'},headers=self.headers)
        stored = self.wait(job_id)['rows'][0]
        self.assertEqual((stored['short_remark'],stored['remark_color'],stored['remark']),('待联系','#285d9b','只改详细内容'))
        self.assertEqual(stored['edited'],0)
        self.client.put(path,json={'short_remark':''},headers=self.headers)
        self.assertEqual(self.wait(job_id)['rows'][0]['remark'],'只改详细内容')

    def test_invalid_short_remark_or_color_is_atomic(self):
        job_id = self.client.post('/api/demo',json={},headers=self.headers).json['id']
        row = self.wait(job_id)['rows'][0]
        path = f"/api/jobs/{job_id}/rows/{row['id']}/remark"
        for payload in ({'short_remark':'字'*51},{'short_remark':'第一行\n第二行'},{'remark_color':'red'},{'remark_color':'#fff'},{'remark_color':'#123456;display:none'}):
            result = self.client.put(path,json={'remark':'不能保存',**payload},headers=self.headers)
            self.assertEqual(result.status_code,400)
            self.assertEqual(self.wait(job_id)['rows'][0]['remark'],row['remark'])

    def test_existing_detailed_remark_migrates_without_becoming_short(self):
        store = self.app.extensions['resume_store']
        rows = demo_rows(1)
        rows[0]['remark'] = '旧版本备注原文\n完整保留'
        store.create_job('oldremarks','旧备注批次','演示',rows,demo=True)
        with store.connect() as conn:
            conn.execute('ALTER TABLE candidates DROP COLUMN short_remark')
            conn.execute('ALTER TABLE candidates DROP COLUMN remark_color')
        migrated = Store(self.folder.name).get_job('oldremarks')['rows'][0]
        self.assertEqual(migrated['remark'],'旧版本备注原文\n完整保留')
        self.assertEqual(migrated['short_remark'],'')
        self.assertEqual(migrated['remark_color'],'#18594c')

    def test_long_remark_pdf_keeps_tail_across_pages(self):
        store = self.app.extensions['resume_store']
        rows = demo_rows(1)
        remark = '备注内容'*1240 + '备注末尾标记'
        rows[0]['remark'] = remark
        store.create_job('longremark','长备注验证','演示',rows,demo=True)
        pdf = self.client.get('/api/jobs/longremark/export/pdf',headers=self.headers)
        self.assertEqual(pdf.status_code,200)
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf.data))
        text = ''.join(p.extract_text() for p in reader.pages)
        self.assertIn('备注末尾标记',text)

    def test_old_database_migrates_remarks_without_losing_candidates(self):
        store = self.app.extensions['resume_store']
        rows = demo_rows(1)
        store.create_job('legacy','旧批次','test',rows,demo=True)
        with store.connect() as conn:
            conn.execute('ALTER TABLE candidates DROP COLUMN remark')
        migrated = Store(self.folder.name)
        result = migrated.get_job('legacy')
        self.assertEqual(result['rows'][0]['data']['name'],rows[0]['data']['name'])
        self.assertEqual(result['rows'][0]['remark'],'')
        migrated.update_row(rows[0]['id'],remark='重启后保留的备注')
        self.assertEqual(Store(self.folder.name).get_job('legacy')['rows'][0]['remark'],'重启后保留的备注')

    def test_restart_recovery(self):
        store = self.app.extensions["resume_store"]
        rows = demo_rows(2)
        rows[1].update(status="processing", data=None)
        store.create_job("abc", "重启恢复测试", "test", rows)
        store.recover()
        job = store.get_job("abc")
        self.assertEqual(job["stats"]["done"], 1)
        self.assertEqual(job["stats"]["failed"], 1)
        self.assertEqual(job["status"], "completed")

    def test_talent_accumulates_three_then_one_and_survives_restart(self):
        first = self.wait(self.upload([self.resume('数据库甲'),self.resume('数据库乙'),self.resume('数据库丙')]))
        library = self.client.get('/api/talent',headers=self.headers).json
        self.assertEqual(library['total'],3)
        self.assertEqual(library['matched'],3)
        self.assertEqual({r['data']['name'] for r in library['rows']},{'数据库甲','数据库乙','数据库丙'})
        first_ids = {r['id'] for r in library['rows']}
        self.wait(self.upload([self.resume('数据库丁')]))
        library = self.client.get('/api/talent',headers=self.headers).json
        self.assertEqual(library['total'],4)
        self.assertEqual(ModelHandler.calls,4)
        store = Store(self.folder.name)
        self.assertEqual(store.list_talent()['total'],4)
        self.assertTrue(first_ids.issubset({r['id'] for r in store.list_talent()['rows']}))
        row = first['rows'][0]
        self.client.post(f"/api/jobs/{first['id']}/education",json=self.config,headers=self.headers)
        self.wait(first['id'])
        self.assertEqual(self.client.get('/api/talent/counts',headers=self.headers).json['total'],4)
        # Updates of the same successful row do not add extra library records.
        self.assertEqual(store.get_talent(row['id'])['data']['name'],'数据库甲')

    def test_talent_excludes_demo_failed_and_same_batch_duplicates(self):
        self.client.post('/api/demo',json={},headers=self.headers)
        self.assertEqual(self.client.get('/api/talent/counts',headers=self.headers).json['total'],0)
        same = self.resume('重复入库')[0].getvalue()
        self.wait(self.upload([(io.BytesIO(same),'一.txt'),(io.BytesIO(same),'二.txt'),self.resume('失败入库','SIMULATED_FAILURE')]))
        library = self.client.get('/api/talent',headers=self.headers).json
        self.assertEqual(library['total'],1)
        self.assertEqual(library['rows'][0]['data']['name'],'重复入库')

    def test_talent_crud_remarks_recycle_and_batch_deletion_are_independent(self):
        job = self.wait(self.upload([self.resume('入库候选人')]))
        row = job['rows'][0]
        talent_path = f"/api/talent/{row['id']}"
        details = self.client.get(talent_path,headers=self.headers).json
        edited = {**details['data'],'location':'杭州','_short_remark':'待联系','_remark_color':'#b33a32','_remark':'完整沟通记录'}
        self.assertEqual(self.client.put(talent_path,json=edited,headers=self.headers).status_code,200)
        source = self.wait(job['id'])['rows'][0]
        self.assertEqual(source['data']['location'],'杭州')
        self.assertEqual(source['short_remark'],'待联系')
        self.assertEqual(source['remark'],'完整沟通记录')
        self.client.put(talent_path+'/remark',json={'remark':'备注更新'},headers=self.headers)
        self.assertEqual(self.wait(job['id'])['rows'][0]['remark'],'备注更新')
        self.assertEqual(self.client.delete(talent_path,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/talent',headers=self.headers).json['total'],0)
        self.assertEqual(self.client.get('/api/talent?deleted=1',headers=self.headers).json['matched'],1)
        self.assertEqual(Store(self.folder.name).list_talent()['total'],0)
        self.assertEqual(self.client.put(talent_path,json=edited,headers=self.headers).status_code,400)
        self.client.post(talent_path+'/restore',headers=self.headers)
        self.assertEqual(self.client.get('/api/talent/counts',headers=self.headers).json['total'],1)
        self.client.delete(f"/api/jobs/{job['id']}",headers=self.headers)
        remaining = self.client.get(talent_path,headers=self.headers).json
        self.assertEqual(remaining['data']['name'],'入库候选人')
        self.assertEqual(remaining['remark'],'备注更新')
        self.assertTrue(remaining['raw_text'])
        self.assertTrue(remaining['can_edit'])
        self.assertEqual(self.client.put(talent_path,json={**remaining['data'],'target_role':'手动维护岗位'},headers=self.headers).status_code,200)
        self.assertEqual(ModelHandler.calls,1)

    def test_talent_manual_add_search_degree_paging_and_exports(self):
        self.assertEqual(self.client.get('/api/talent').status_code,403)
        self.assertEqual(self.client.post('/api/talent',json={'name':''},headers=self.headers).status_code,400)
        ids = []
        for i in range(3):
            candidate = {'name':f'手动候选人{i}','phone':f'00123{i}','location':'上海','education':[{'level':'本科','school':'测试大学','major':'计算机'}], '_short_remark':'待联系','_remark':'人工备注','_remark_color':'#285d9b'}
            result = self.client.post('/api/talent',json=candidate,headers=self.headers)
            self.assertEqual(result.status_code,201)
            ids.append(result.json['id'])
        page = self.client.get('/api/talent?page_size=2&page=2',headers=self.headers).json
        self.assertEqual((page['total'],page['matched'],page['pages'],len(page['rows'])),(3,3,2,1))
        search = self.client.get('/api/talent?q=手动候选人1&degree=本科',headers=self.headers).json
        self.assertEqual(search['matched'],1)
        self.assertEqual(search['rows'][0]['id'],ids[1])
        self.assertEqual(self.client.get('/api/talent?q=人工备注',headers=self.headers).json['matched'],3)
        self.assertEqual(self.client.get('/api/talent?q=%25',headers=self.headers).json['matched'],0)
        xlsx = self.client.get('/api/talent/export/xlsx?q=手动候选人1',headers=self.headers)
        self.assertEqual(xlsx.status_code,200)
        with zipfile.ZipFile(io.BytesIO(xlsx.data)) as archive:
            text = archive.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('手动候选人1',text)
            self.assertNotIn('手动候选人0',text)
            self.assertIn('人工备注',text)
        pdf = self.client.get('/api/talent/export/pdf',headers=self.headers)
        from pypdf import PdfReader
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.data)).pages)
        for name in ('手动候选人0','手动候选人1','手动候选人2'):
            self.assertIn(name,text)
        self.assertEqual(ModelHandler.calls,0)

    def test_talent_legacy_migration_preserves_source_and_deleted_records(self):
        store = self.app.extensions['resume_store']
        rows = demo_rows(2)
        store.create_job('historical','已有真实批次','test',rows,demo=False)
        store.set_status('historical','completed')
        with store.connect() as conn:
            conn.execute('DROP TABLE talent')
        migrated = Store(self.folder.name)
        self.assertEqual(migrated.list_talent()['total'],2)
        self.assertEqual(migrated.get_talent(rows[1]['id'])['remark'],rows[1]['remark'])
        migrated.remove_talent(rows[0]['id'])
        self.assertEqual(Store(self.folder.name).list_talent()['total'],1)
        source = migrated.get_job('historical')['rows'][0]
        self.assertEqual(source['data'],rows[0]['data'])

    def test_talent_edit_is_blocked_during_source_update_but_remarks_are_preserved(self):
        job = self.wait(self.upload([self.resume('更新期间候选人')]))
        row = job['rows'][0]
        store = self.app.extensions['resume_store']
        store.set_status(job['id'],'running')
        path = f"/api/talent/{row['id']}"
        self.assertFalse(self.client.get(path,headers=self.headers).json['can_edit'])
        self.assertEqual(self.client.put(path,json={**row['data'],'name':'不能保存'},headers=self.headers).status_code,400)
        self.assertEqual(self.client.put(path+'/remark',json={'remark':'更新期间写的备注'},headers=self.headers).status_code,200)
        store.update_row(row['id'],data=row['data'],status='done')
        self.assertEqual(store.get_talent(row['id'])['remark'],'更新期间写的备注')
        store.set_status(job['id'],'completed')


if __name__ == "__main__":
    unittest.main()
