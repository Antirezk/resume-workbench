"""Generate disposable synthetic reports and exercise real Chinese OCR."""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pypdf import PdfReader
import pypdfium2

from resume_tool.demo import demo_rows
from resume_tool.extraction import extract_text
from resume_tool.reports import chinese_font, export_excel, export_pdf
from resume_tool.schema import derived, statistics


root = Path(__file__).resolve().parent.parent
output = root / "output" / "verification"
output.mkdir(parents=True, exist_ok=True)
rows = demo_rows(50)
for row in rows:
    row.update(derived=derived(row["data"]), edited=0, error="", duplicate_of="")
job = {"id": "synthetic-verification", "title": "50 份中文简历汇总（虚构数据）", "model": "文件导出验证 / 未调用 API", "is_demo": 1, "rows": rows, "stats": statistics(rows)}
(output / "中文简历汇总_演示.pdf").write_bytes(export_pdf(job))
(output / "中文简历整理_演示.xlsx").write_bytes(export_excel(job))
reader = PdfReader(output / "中文简历汇总_演示.pdf")
text = "\n".join(page.extract_text() for page in reader.pages)
assert "林致远50" in text
print(json.dumps({"pdf_pages": len(reader.pages), "rows": len(rows), "last_candidate_present": True}, ensure_ascii=False))

font = chinese_font()
path = output / "测试简历_文字.pdf"
pdf = canvas.Canvas(str(path), pagesize=A4)
pdf.setFont(font, 15)
lines = ["测试简历", "姓名：张明", "城市：上海", "邮箱：demo@example.invalid", "教育经历：测试大学 本科 计算机科学",
         "工作经历：2020-01 至 2023-12 测试科技公司", "职位：软件工程师", "技能：Python SQL 数据分析", "负责业务接口开发与数据处理。"]
for i, line in enumerate(lines):
    pdf.drawString(45, 770 - 40 * i, line)
pdf.save()
result = extract_text(path)
assert "张明" in result["text"] and "Python" in result["text"]
doc = pypdfium2.PdfDocument(str(path))
page = doc[0]
bitmap = page.render(scale=2)
image = bitmap.to_pil().copy()
image.save(output / "测试简历_扫描.png")
bitmap.close()
page.close()
doc.close()
scan_path = output / "测试简历_扫描.pdf"
pdf = canvas.Canvas(str(scan_path), pagesize=A4)
pdf.drawImage(str(output / "测试简历_扫描.png"), 0, 0, width=A4[0], height=A4[1])
pdf.save()
ocr_result = extract_text(scan_path, use_ocr=True)
assert "张明" in ocr_result["text"], ocr_result["text"]
assert "Python" in ocr_result["text"], ocr_result["text"]
(output / "ocr_result.txt").write_text(ocr_result["text"], encoding="utf-8")
print(json.dumps({"ocr": "passed", "method": ocr_result["method"], "chars": len(ocr_result["text"])}, ensure_ascii=False))
