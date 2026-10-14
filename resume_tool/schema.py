import calendar
import re
from collections import Counter
from datetime import date, datetime, timezone, timedelta


EXAMPLE = {
    "name": "", "phone": "", "email": "", "location": "", "target_role": "",
    "summary": "", "skills": [], "target_city": "", "job_status": "", "availability": "", "claimed_work_years": "",
    "education": [{"level": "", "school": "", "college": "", "degree": "", "major": "", "study_type": "",
                   "start_date": "", "end_date": "", "graduation_status": "", "research_direction": "",
                   "academic_details": "", "source_text": ""}],
    "experience": [{"company": "", "title": "", "employment_type": "未明确", "department": "", "location": "", "start_date": "", "end_date": "", "claimed_duration": "", "description": "", "achievements": "", "source_text": "", "is_internship": False}],
    "projects": [{"name": "", "role": "", "start_date": "", "end_date": "", "description": "", "technologies": "", "achievements": "", "source_text": ""}],
    "qualifications": [{"category": "", "name": "", "issuer": "", "date": "", "details": "", "source_text": ""}],
    "languages": [{"name": "", "level": "", "test_score": ""}],
    "links": [{"label": "", "url": ""}],
    "evidence": [{"field": "name", "quote": "简历中的对应原文"}],
    "notes": [],
}
SCALARS = ("name", "phone", "email", "location", "target_role", "summary", "target_city", "job_status", "availability", "claimed_work_years")
LIST_FIELDS = {
    "education": ("level", "school", "college", "degree", "major", "study_type", "start_date", "end_date",
                  "graduation_status", "research_direction", "academic_details", "source_text"),
    "experience": ("company", "title", "employment_type", "department", "location", "start_date", "end_date", "claimed_duration", "description", "achievements", "source_text"),
    "projects": ("name", "role", "start_date", "end_date", "description", "technologies", "achievements", "source_text"),
    "qualifications": ("category", "name", "issuer", "date", "details", "source_text"),
    "languages": ("name", "level", "test_score"), "links": ("label", "url"), "evidence": ("field", "quote"),
}
EMPTY_VALUES = {"未提供", "未知", "不详", "无", "null", "None", "N/A"}
EDUCATION_RANK = {"博士": 6, "硕士": 5, "本科": 4, "大专": 3, "高中/中专": 2, "其他": 1, "未提供": 0}


def education_level(row):
    """Identify only explicit levels; ambiguous postgraduate study stays ambiguous."""
    level = (row.get("level") or "").strip()
    if level in EDUCATION_RANK and level != "其他":
        return level
    degree = (row.get("degree") or "").casefold()
    for label, keywords in (
        ("博士", ("博士", "phd", "ph.d", "doctor")),
        ("硕士", ("硕士", "master", "mba", "m.sc", "msc")),
        ("本科", ("本科", "学士", "bachelor", "b.sc", "bsc")),
        ("大专", ("大专", "专科", "associate")),
        ("高中/中专", ("高中", "中专", "high school")),
    ):
        if any(keyword in degree for keyword in keywords):
            return label
    if "研究生" in degree and not level:
        return "研究生（层次未明确）"
    return level or ("其他" if degree else "未提供")


def education_summary(candidate, level=None):
    lines = []
    for row in candidate["education"]:
        label = education_level(row)
        if level and label != level:
            continue
        school, major = row.get("school") or "院校未提供", row.get("major") or "专业未提供"
        status = "（" + row["graduation_status"] + "）" if row.get("graduation_status") else ""
        lines.append(f"{label}{status}：{school} / {major}")
    return "\n".join(lines)


def clean_string(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("模型字段格式不正确：文字字段必须是字符串")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()
    if len(value) > 20000:
        raise ValueError("单个字段过长，请检查模型输出")
    return "" if value in EMPTY_VALUES else value


def normalize_candidate(data):
    if not isinstance(data, dict) or not any(k in data for k in SCALARS):
        raise ValueError("模型没有返回有效的候选人 JSON 对象")
    result = {key: clean_string(data.get(key)) for key in SCALARS}
    for key in ("skills", "notes"):
        values = data.get(key) or []
        if not isinstance(values, list) or len(values) > 150:
            raise ValueError(f"{key} 必须是长度不超过 150 的列表")
        unique = {}
        for value in values:
            value = clean_string(value)
            if value:
                unique.setdefault(value.casefold(), value)
        result[key] = list(unique.values())
    for key, fields in LIST_FIELDS.items():
        items = data.get(key) or []
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError(f"{key} 必须是长度不超过 100 的列表")
        result[key] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{key} 中的每条记录必须是对象")
            row = {field: clean_string(item.get(field)) for field in fields}
            if not any(row.values()):
                continue
            if key == "education":
                row["level"] = education_level(row)
            if key == "experience":
                internship = item.get("is_internship", False)
                if not isinstance(internship, bool):
                    raise ValueError("is_internship 必须是布尔值")
                # Older records only had the boolean flag; preserve their classification.
                kind = row["employment_type"] or ("实习" if internship else "工作")
                if kind not in {"工作", "实习", "兼职", "劳务/自由职业", "未明确"}:
                    raise ValueError("经历性质必须是工作、实习、兼职、劳务/自由职业或未明确")
                row["employment_type"] = kind
                row["is_internship"] = kind == "实习"
            if any(row.get(field) for field in fields):
                result[key].append(row)
    return result


def month_number(text, today=None):
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    text = text.strip()
    if text.casefold() in {"至今", "现在", "目前", "present", "current", "now"}:
        return today.year * 12 + today.month - 1
    match = re.fullmatch(r"(19\d{2}|20\d{2})[-./年](\d{1,2})(?:月(?:([0-9]{1,2})日)?|[-./]([0-9]{1,2}))?", text)
    if not match:
        return None
    year, month = map(int, match.groups()[:2])
    try:
        date(year, month, int(match.group(3) or match.group(4) or 1))
    except ValueError:
        return None
    return year * 12 + month - 1


def duration(row, today=None):
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    start, end = month_number(row["start_date"], today), month_number(row["end_date"], today)
    current = today.year * 12 + today.month - 1
    def exact_day(text):
        match = re.fullmatch(r"(19\d{2}|20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})日?", text)
        if match:
            try:
                return date(*map(int, match.groups()))
            except ValueError:
                return None
        return today if text.casefold() in {"至今", "现在", "目前", "present", "current", "now"} else None
    start_day, end_day = exact_day(row["start_date"]), exact_day(row["end_date"])
    if start is None or end is None:
        reason = "日期缺失或未明确到月份"
    elif start > end or (start_day and end_day and start_day > end_day):
        reason = "开始日期晚于结束日期"
    elif end > current or (end_day and end_day > today) or (start_day and start_day > today):
        reason = "含未来日期，不计入已发生年限"
    else:
        months = end - start + 1
        years, remainder = divmod(months, 12)
        label = (f"{years}年" if years else "") + (f"{remainder}个月" if remainder else "")
        return {"months": months, "years": round(months / 12, 2), "label": label, "warning": "", "start": start, "end": end + 1}
    return {"months": None, "years": None, "label": "未能计算", "warning": reason, "start": None, "end": None}


def merged_months(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def experience_summary(candidate, info, kind):
    return "\n".join(f"{item['company'] or '公司未提供'} / {item['title'] or '岗位未提供'}；{item['start_date'] or '未提供'} - {item['end_date'] or '未提供'}；{info['experience_details'][i]['label']}"
                     for i, item in enumerate(candidate['experience']) if item['employment_type'] == kind)


def highest_degree(candidate):
    levels = [education_level(row) for row in candidate["education"]]
    if "研究生（层次未明确）" in levels and not any(level in {"硕士", "博士"} for level in levels):
        return "研究生（层次未明确）"
    return max(levels, key=lambda value: EDUCATION_RANK.get(value, 1)) if levels else "未提供"


def derived(candidate, today=None):
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    current = today.year * 12 + today.month - 1
    ranges, notes, incomplete = [], list(candidate["notes"]), False
    internship_ranges, internship_partial, details = [], False, []
    for row in candidate["experience"]:
        info = duration(row, today)
        details.append(info)
        kind = row["employment_type"]
        label = f"{kind}：{row['company'] or '公司未提供'} / {row['title'] or '岗位未提供'}"
        if not row["company"] or not row["title"]:
            notes.append(label + "，公司或岗位名称缺失")
        if info["warning"]:
            notes.append("任职日期需核对：" + label + "；" + info["warning"])
        if kind == "未明确":
            notes.append(label + "，请核对经历性质；未计入工作或实习累计年限")
            incomplete = internship_partial = True
        if kind not in {"工作", "实习"}:
            continue
        if info["months"] is None:
            if kind == "工作":
                incomplete = True
            else:
                internship_partial = True
        else:
            (ranges if kind == "工作" else internship_ranges).append((info["start"], info["end"]))
    months, internship_months = merged_months(ranges), merged_months(internship_ranges)
    for label, intervals in (("工作", ranges), ("实习", internship_ranges)):
        if sum(end - start for start, end in intervals) > merged_months(intervals):
            notes.append(f"{label}时间存在重叠，累计年限已合并重叠月份，请核对是否同期任职或岗位变动")
    if not candidate["name"]:
        notes.append("姓名未提取，请人工补充")
    if not candidate["phone"] and not candidate["email"]:
        notes.append("联系电话和邮箱均未提取，请核对原文")
    for education in candidate["education"]:
        level = education_level(education)
        missing = [label for key, label in (("school", "院校"), ("major", "专业")) if not education.get(key)]
        if missing:
            notes.append(f"{level}教育经历缺少{'、'.join(missing)}，请核对原文")
        if level in {"未提供", "研究生（层次未明确）"}:
            notes.append("教育阶段未明确，请核对本科、硕士或博士层次")
    return {
        "highest_degree": highest_degree(candidate),
        "education_summary": education_summary(candidate),
        "experience_years": round(months / 12, 1) if ranges else None,
        "experience_months": months if ranges else None,
        "internship_months": internship_months if internship_ranges else None,
        "internship_years": round(internship_months / 12, 2) if internship_ranges else None,
        "internship_partial": internship_partial, "experience_details": details,
        "years_partial": incomplete, "warnings": list(dict.fromkeys(notes)),
    }


def statistics(rows):
    successful = [row for row in rows if row["status"] == "done"]
    education = Counter(row["derived"]["highest_degree"] for row in successful)
    skills = Counter(skill for row in successful for skill in row["data"]["skills"])
    known_years = [row["derived"]["experience_years"] for row in successful
                   if row["derived"]["experience_years"] is not None and not row["derived"]["years_partial"]]
    return {
        "total": len(rows), "done": len(successful),
        "failed": sum(row["status"] == "failed" for row in rows),
        "duplicates": sum(row["status"] == "duplicate" for row in rows),
        "review": sum(bool(row["derived"]["warnings"]) for row in successful),
        "average_years": round(sum(known_years) / len(known_years), 1) if known_years else None,
        "years_sample_size": len(known_years), "education": dict(education),
        "top_skills": skills.most_common(10),
    }
