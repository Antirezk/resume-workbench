"""Portable XLSX serialization and Chinese PDF reports for the application."""
import io
import re
import math
import unicodedata
from collections import Counter
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZipFile, ZIP_DEFLATED

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from .schema import education_level, education_summary, experience_summary


STATUS = {"done": "成功", "failed": "失败", "duplicate": "重复文件", "queued": "等待", "processing": "处理中", "canceled": "已停止"}


def remark_color(row):
    value = row.get("remark_color") or ""
    return value.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else "#18594c"


def years_label(row):
    info = row["derived"]
    if info["experience_years"] is None:
        return "未能计算"
    return ("已知经历 " if info["years_partial"] else "") + str(info["experience_years"]) + " 年"


def column_name(index):
    name = ""
    while index:
        index, digit = divmod(index - 1, 26)
        name = chr(65 + digit) + name
    return name


def xml_text(value):
    return xml_escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)))


def sheet_xml(rows, widths, cell_styles=None):
    end_column = column_name(len(rows[0]))
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             f'<dimension ref="A1:{end_column}{len(rows)}"/>',
             '<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>',
             '<sheetFormatPr defaultRowHeight="24"/><cols>']
    parts.extend(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    parts.append('</cols><sheetData>')
    for index, row in enumerate(rows, 1):
        line_counts = []
        for value, width in zip(row, widths):
            lines = str(value if value is not None else "").split("\n")
            line_counts.append(sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in line) / max(1, width - 3))) for line in lines))
        height = 33 if index == 1 else min(409, max(28, max(line_counts) * 16 + 8))
        parts.append(f'<row r="{index}" ht="{height}" customHeight="1">')
        for col, value in enumerate(row, 1):
            reference = column_name(col) + str(index)
            style = 1 if index == 1 else (2 if index % 2 == 0 else 0)
            style = (cell_styles or {}).get(reference, style)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f'<c r="{reference}" s="{style}" t="n"><v>{value}</v></c>')
            else:
                # Explicit inline strings keep phone numbers and formula-like names literal.
                parts.append(f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value if value is not None else "")}</t></is></c>')
        parts.append('</row>')
    parts.extend(['</sheetData>', f'<autoFilter ref="A1:{end_column}{len(rows)}"/>',
                  '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>', '</worksheet>'])
    return "".join(parts)


STYLES_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FF24354B"/></font><font><b/><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FFFFFFFF"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF184F45"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF1F6F4"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def short_remark_styles(success):
    palette = list(dict.fromkeys(remark_color(row) for row in success if row.get("short_remark")))
    fonts, formats, cell_styles = [], [], {}
    for i, color in enumerate(palette):
        fonts.append(f'<font><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FF{color[1:].upper()}"/></font>')
        for fill in (0, 3):
            formats.append(f'<xf numFmtId="0" fontId="{2+i}" fillId="{fill}" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>')
    for index, row in enumerate(success, 2):
        if row.get("short_remark"):
            cell_styles[f"O{index}"] = 3 + 2 * palette.index(remark_color(row)) + int(index % 2 == 0)
    styles = STYLES_XML.replace('<fonts count="2">', f'<fonts count="{2+len(palette)}">').replace('</fonts>', ''.join(fonts) + '</fonts>')
    styles = styles.replace('<cellXfs count="3">', f'<cellXfs count="{3+len(formats)}">').replace('</cellXfs>', ''.join(formats) + '</cellXfs>')
    return styles, cell_styles


def export_excel(job):
    success = [row for row in job["rows"] if row["status"] == "done"]
    total = [["姓名", "联系电话", "邮箱", "城市", "求职意向", "最高教育阶段（含在读）", "工作年限（年）", "年限是否仅含已知经历", "技能", "经历摘要", "需核对事项", "来源文件", "已人工修改", "详细备注（人工填写）", "短备注（姓名下方）", "短备注字体颜色"] +
             [level + label for level in ("本科", "硕士", "博士") for label in ("院校", "专业", "学历/学位原文", "就读时间", "毕业/在读状态")] + ["全部教育阶段汇总", "工作累计月数", "实习累计月数", "实习累计年数", "实习是否仅含已知经历", "全部工作公司岗位与时长", "全部实习公司岗位与时长", "自述工作年限（原文）", "期望工作城市", "求职/任职状态（原文）", "到岗时间（原文）", "证书奖项与学术成果", "语言与成绩", "作品集与个人主页", "时长计算口径"]]
    exp = [["姓名", "公司", "岗位名称", "开始日期（原文）", "结束日期（原文）", "月数", "年数", "时长", "部门", "地点", "职责", "成果与量化业绩", "自述时长（原文）", "日期核对提示", "经历原文", "来源文件"]]
    internships, other = [exp[0].copy()], [exp[0][:1] + ["经历性质"] + exp[0][1:]]
    edu = [["姓名", "教育阶段", "就读院校", "学院/院系", "专业", "学历/学位原文", "学习形式", "开始日期（原文/年月）", "结束日期（原文/年月）", "毕业/在读状态", "研究方向", "学术与学习详情", "教育经历原文", "来源文件"]]
    projects = [["姓名", "项目", "本人角色", "开始日期", "结束日期", "项目内容", "技术栈", "成果", "项目原文", "来源文件"]]
    qualifications = [["姓名", "类别", "名称", "机构", "日期", "等级/编号/详情", "原文证据", "来源文件"]]
    languages = [["姓名", "语种", "水平（原文）", "考试与成绩", "来源文件"]]
    links = [["姓名", "名称", "网址（原文）", "来源文件"]]
    evidence = [["姓名", "字段", "原文证据", "来源文件"]]
    failures = [["来源文件", "处理状态", "错误/重复来源"]]
    for row in job["rows"]:
        if row["status"] != "done":
            duplicate = next((r["filename"] for r in job["rows"] if r["id"] == row["duplicate_of"]), "")
            failures.append([row["filename"], STATUS.get(row["status"], row["status"]), row["error"] or duplicate])
    for row in success:
        data, info = row["data"], row["derived"]
        level_columns = []
        for level in ("本科", "硕士", "博士"):
            items = [item for item in data["education"] if education_level(item) == level]
            for key in ("school", "major", "degree", "period", "graduation_status"):
                level_columns.append("\n".join((f"{item.get('start_date') or '未提供'} - {item.get('end_date') or '未提供'}" if key == "period" else item.get(key) or "未提供") for item in items))
        total.append([data["name"] or "未提供", data["phone"], data["email"], data["location"], data["target_role"], info["highest_degree"],
                      info["experience_years"], "是" if info["years_partial"] else "否", "、".join(data["skills"]), data["summary"],
                      "\n".join(info["warnings"]), row["filename"], "是" if row["edited"] else "否", row.get("remark", ""), row.get("short_remark", ""), remark_color(row)] + level_columns + [education_summary(data), info["experience_months"], info["internship_months"], info["internship_years"], "是" if info["internship_partial"] else "否", experience_summary(data, info, "工作"), experience_summary(data, info, "实习"), data["claimed_work_years"], data["target_city"], data["job_status"], data["availability"], "\n".join(" / ".join(item[k] for k in ("category", "name", "issuer", "date", "details") if item[k]) for item in data["qualifications"]), "\n".join(" / ".join(item.values()) for item in data["languages"]), "\n".join(" / ".join(item.values()) for item in data["links"]), "含首尾月；同类重叠月份去重；工作与实习分开；其他性质不计入两项累计"])
        for i, item in enumerate(data["experience"]):
            timing = info["experience_details"][i]
            record = [data["name"], item["company"], item["title"], item["start_date"], item["end_date"], timing["months"], timing["years"], timing["label"], item["department"], item["location"], item["description"], item["achievements"], item["claimed_duration"], timing["warning"], item["source_text"], row["filename"]]
            if item["employment_type"] == "工作":
                exp.append(record)
            elif item["employment_type"] == "实习":
                internships.append(record)
            else:
                other.append(record[:1] + [item["employment_type"]] + record[1:])
        for item in data["education"]:
            edu.append([data["name"], education_level(item)] + [item.get(key) or "未提供" for key in ("school", "college", "major", "degree", "study_type", "start_date", "end_date", "graduation_status", "research_direction", "academic_details", "source_text")] + [row["filename"]])
        for item in data["projects"]:
            projects.append([data["name"]] + [item[k] for k in ("name", "role", "start_date", "end_date", "description", "technologies", "achievements", "source_text")] + [row["filename"]])
        for section, target, keys in (("qualifications", qualifications, ("category", "name", "issuer", "date", "details", "source_text")), ("languages", languages, ("name", "level", "test_score")), ("links", links, ("label", "url"))):
            for item in data[section]:
                target.append([data["name"]] + [item[k] for k in keys] + [row["filename"]])
        for item in data["evidence"]:
            evidence.append([data["name"], item["field"], item["quote"], row["filename"]])
    career_widths = [16, 32, 28, 24, 24, 12, 12, 20, 28, 20, 75, 65, 24, 44, 85, 35]
    sheets = [("候选人总表", total, [16, 20, 30, 16, 24, 30, 19, 22, 44, 65, 48, 35, 18, 70, 30, 22] + [32, 30, 28, 30, 22] * 3 + [70, 18, 18, 18, 24, 85, 85, 30, 24, 30, 30, 65, 48, 65, 65]),
              ("工作经历", exp, career_widths),
              ("教育经历", edu, [16, 24, 35, 35, 32, 28, 22, 24, 24, 22, 45, 70, 85, 35]),
              ("项目经历", projects, [16, 35, 28, 24, 24, 80, 40, 65, 85, 35]),
              ("原文证据", evidence, [16, 24, 85, 35]),
              ("未成功与重复文件", failures, [40, 18, 85]),
              ("实习经历", internships, career_widths), ("其他任职经历", other, [16, 24] + career_widths[1:]),
              ("证书奖项与学术成果", qualifications, [16, 24, 45, 35, 24, 65, 85, 35]),
              ("语言能力", languages, [16, 24, 35, 45, 35]), ("作品集与个人主页", links, [16, 35, 85, 35])]
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + overrides + '</Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{xml_text(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, rows, widths) in enumerate(sheets, 1)) + '</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        styles, colored_cells = short_remark_styles(success)
        archive.writestr("xl/styles.xml", styles)
        for i, (_, rows, widths) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows, widths, colored_cells if i == 1 else None))
    return output.getvalue()


def chinese_font():
    if "ResumeChinese" in pdfmetrics.getRegisteredFontNames():
        return "ResumeChinese"
    for path in (Path(__file__).parent.parent / "fonts" / "NotoSansSC-Regular.ttf", Path("C:/Windows/Fonts/msyh.ttc")):
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont("ResumeChinese", str(path), subfontIndex=0))
                return "ResumeChinese"
            except Exception:
                continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def export_pdf(job):
    output = io.BytesIO()
    font = chinese_font()
    ink, green, gray = colors.HexColor("#21362f"), colors.HexColor("#18594c"), colors.HexColor("#65766e")
    page_size = landscape(A4)
    doc = SimpleDocTemplate(output, pagesize=page_size, rightMargin=17 * mm, leftMargin=17 * mm,
                            topMargin=17 * mm, bottomMargin=16 * mm, title="中文简历汇总报告", author="简历工作台")
    styles = {
        "title": ParagraphStyle("title", fontName=font, fontSize=22, leading=31, textColor=ink, spaceAfter=9),
        "h2": ParagraphStyle("h2", fontName=font, fontSize=13, leading=20, textColor=green, spaceBefore=10, spaceAfter=7),
        "body": ParagraphStyle("body", fontName=font, fontSize=9, leading=15, textColor=ink, wordWrap="CJK", spaceAfter=5),
        "small": ParagraphStyle("small", fontName=font, fontSize=7.5, leading=12, textColor=gray, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=8, leading=12, textColor=ink, wordWrap="CJK"),
        "header": ParagraphStyle("header", fontName=font, fontSize=8, leading=12, textColor=colors.white, wordWrap="CJK"),
    }
    def para(value, kind="body"):
        return Paragraph(escape(str(value or "未提供")).replace("\n", "<br/>"), styles[kind])

    def candidate_name(row):
        name = escape(row["data"]["name"] or "未提供姓名")
        if row.get("short_remark"):
            name += f'<br/><font color="{remark_color(row)}">{escape(row["short_remark"])}</font>'
        return Paragraph(name, styles["cell"])

    def table(headers, rows, widths):
        data = [[para(h, "header") for h in headers]] + [[value if isinstance(value, Paragraph) else para(value, "cell") for value in row] for row in rows]
        result = Table(data, colWidths=[width * mm for width in widths], repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
        result.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), green), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f5f2")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, green),
        ]))
        return result

    stats = job["stats"]
    success = [row for row in job["rows"] if row["status"] == "done"]
    generated = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    story = [para("中文简历汇总报告", "title"), para(job["title"] + ("（演示数据）" if job["is_demo"] else ""), "body"),
             para(f"生成时间：{generated}（UTC+8）　模型：{job['model']}　批次：{job['id'][:8]}", "small"), Spacer(1, 10)]
    counts = f"上传 {stats['total']} 份　成功 {stats['done']} 份　失败 {stats['failed']} 份　重复文件 {stats['duplicates']} 份　需核对 {stats['review']} 份"
    story.extend([para(counts, "body"), para("统计按成功解析的简历份数计；联系方式重复的简历保留，并在核对事项中提示。", "small")])
    if stats["average_years"] is not None:
        story.append(para(f"可完整计算任职日期的 {stats['years_sample_size']} 份简历，平均工作年限为 {stats['average_years']} 年。", "body"))
    story.append(para("时长按起止月份计算（含首尾月，非精确天数），同类重叠月份去重。工作与实习分别累计；兼职、劳务及性质未明确的经历单独保留，不计入两项累计。只有年份、缺失或无效日期不计算。", "small"))
    story.append(para("学历与技能分布", "h2"))
    summary_rows = []
    degrees, skills = list(stats["education"].items()), stats["top_skills"]
    for i in range(max(len(degrees), len(skills), 1)):
        degree = degrees[i] if i < len(degrees) else ("", "")
        skill = skills[i] if i < len(skills) else ("", "")
        summary_rows.append([degree[0] or "-", degree[1] if degree[0] else "-", skill[0] or "-", skill[1] if skill[0] else "-"])
    story.append(para("最高教育阶段包括在读经历；具体学位是否取得以教育明细中的原文和毕业状态为准。", "small"))
    story.append(table(["最高教育阶段", "简历份数", "高频技能", "出现份数"], summary_rows, [75, 45, 100, 42]))
    story.append(para("候选人总览", "h2"))
    if success:
        overview = [[candidate_name(row), row["data"]["location"], row["derived"]["highest_degree"], years_label(row),
                     row["data"]["target_role"], "、".join(row["data"]["skills"][:8])] for row in success]
        story.append(table(["姓名", "城市", "教育阶段", "工作年限", "求职意向", "主要技能"], overview, [24, 25, 24, 34, 45, 110]))
    else:
        story.append(para("本批次暂无成功解析的简历。"))
    if success:
        story.extend([PageBreak(), para("完整教育经历", "title"),
                      para("本科、硕士、博士等经历逐条列出；同阶段多校或多学位均保留。缺失信息标记为未提供。", "small"), Spacer(1, 8)])
        education_rows = []
        for row in success:
            for item in row["data"]["education"]:
                education_rows.append([row["data"]["name"], education_level(item),
                                       (item.get("school") or "院校未提供") + "\n学院：" + (item.get("college") or "未提供"),
                                       (item.get("major") or "专业未提供") + "\n学位：" + (item.get("degree") or "未提供"),
                                       f"{item.get('start_date') or '未提供'} - {item.get('end_date') or '未提供'}",
                                       "状态：" + (item.get("graduation_status") or "未提供") + "\n形式：" + (item.get("study_type") or "未提供"),
                                       "方向：" + (item.get("research_direction") or "未提供") + "\n详情：" + (item.get("academic_details") or "未提供")])
        if education_rows:
            story.append(table(["姓名", "阶段", "学校与学院", "专业与学位", "就读时间", "修读情况", "研究与学习详情"], education_rows, [22, 18, 58, 44, 32, 30, 58]))
        else:
            story.append(para("本批次成功解析的简历未提供教育经历。"))
        story.extend([PageBreak(), para("工作与实习累计", "title")])
        story.append(table(["姓名", "工作累计", "实习累计", "自述年限（原文）", "求职状态与到岗"], [[r["data"]["name"], years_label(r), (str(r["derived"]["internship_months"]) + "个月" + ("（仅已知）" if r["derived"]["internship_partial"] else "")) if r["derived"]["internship_months"] is not None else "未能计算", r["data"]["claimed_work_years"], "\n".join(r["data"][k] for k in ("target_city", "job_status", "availability") if r["data"][k])] for r in success], [24, 46, 46, 46, 100]))
        for kind, title in (("工作", "完整工作经历"), ("实习", "完整实习经历"), ("其他", "其他任职经历")):
            records = []
            for r in success:
                for i, item in enumerate(r["data"]["experience"]):
                    if (kind == "其他" and item["employment_type"] in {"工作", "实习"}) or (kind != "其他" and item["employment_type"] != kind):
                        continue
                    timing = r["derived"]["experience_details"][i]
                    records.append([r["data"]["name"], item["company"], item["title"] + ("\n" + item["department"] if item["department"] else "") + "\n" + item["employment_type"], (item["start_date"] or "未提供") + " - " + (item["end_date"] or "未提供"), timing["label"] + ("\n" + timing["warning"] if timing["warning"] else "") + ("\n自述：" + item["claimed_duration"] if item["claimed_duration"] else ""), "地点：" + (item["location"] or "未提供") + "\n职责：" + (item["description"] or "未提供") + "\n成果：" + (item["achievements"] or "未提供")])
            story.extend([PageBreak(), para(title, "title")])
            story.append(table(["姓名", "公司", "岗位与部门", "任职时间", "时长与核对", "职责与成果"], records, [22, 42, 36, 38, 34, 90]) if records else para("原文未提供或尚未提取此类经历。"))
        story.extend([PageBreak(), para("项目、证书与补充信息", "title")])
        supplemental = []
        for r in success:
            d = r["data"]
            project_text = "\n\n".join("\n".join([p["name"], "角色：" + (p["role"] or "未提供"), "时间：" + (p["start_date"] or "未提供") + " - " + (p["end_date"] or "未提供"), p["description"], "技术：" + (p["technologies"] or "未提供"), "成果：" + (p["achievements"] or "未提供")]) for p in d["projects"])
            extra = "\n".join(" / ".join(q[k] for k in ("category", "name", "issuer", "date", "details") if q[k]) for q in d["qualifications"])
            extra += "\n语言：" + ("；".join(" / ".join(q.values()) for q in d["languages"]) or "未提供")
            extra += "\n主页：" + ("；".join(" / ".join(q.values()) for q in d["links"]) or "未提供")
            supplemental.append([d["name"], project_text or "未提供项目经历", extra])
        story.append(table(["姓名", "项目与本人贡献", "证书、奖项、语言与主页"], supplemental, [24, 140, 98]))
        story.extend([PageBreak(), para("候选人摘要、备注与核对事项", "title"),
                      para("姓名下方为使用者填写的短备注，颜色按设置保留；详细备注独立列出，与简历提取事实分开。完整经历保留在 Excel；超过 320 字的摘要节选显示。", "small"), Spacer(1, 8)])
        summaries = []
        for row in success:
            data = row["data"]
            summary = data["summary"] or "未提供经历摘要"
            if len(summary) > 320:
                summary = summary[:320] + "…（完整内容见 Excel）"
            contacts = "　".join(value for value in [data["phone"], data["email"]] if value)
            description = summary + "\n来源：" + row["filename"]
            if contacts:
                description += "\n联系：" + contacts
            summaries.append([candidate_name(row), description, row.get("remark") or "未填写", "；".join(row["derived"]["warnings"]) or "无"])
        story.append(table(["姓名与短备注", "经历摘要与来源", "详细备注（人工填写）", "需核对事项"], summaries, [24, 110, 68, 60]))
    incomplete = [row for row in job["rows"] if row["status"] != "done"]
    if incomplete:
        story.extend([PageBreak(), para("未成功与重复文件", "title"), table(["文件", "状态", "说明"],
                      [[r["filename"], STATUS.get(r["status"], r["status"]), r["error"] or
                        ("与 " + next((x["filename"] for x in job["rows"] if x["id"] == r["duplicate_of"]), "同批次文件") + " 内容相同" if r["duplicate_of"] else "处理尚未完成")]
                       for r in incomplete], [70, 25, 167])])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(gray)
        canvas.drawString(17 * mm, 9 * mm, "简历工作台 / " + ("演示报告" if job["is_demo"] else "简历汇总"))
        canvas.drawRightString(page_size[0] - 17 * mm, 9 * mm, str(document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
