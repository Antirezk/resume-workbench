import copy
import uuid

from .schema import normalize_candidate


def demo_rows(count=8):
    templates = [
        ("陈雨桐", "上海", "产品经理", "本科", "华东理工大学", "信息管理", ["需求分析", "SQL", "原型设计", "数据分析"], "2020-07", "澄川科技", "负责企业协作产品的需求梳理与版本规划，推动权限管理和数据看板上线。"),
        ("林致远", "杭州", "后端开发工程师", "硕士", "浙江大学", "计算机技术", ["Python", "SQL", "Docker", "Redis"], "2019-06", "云帆软件", "开发订单处理服务与数据接口，维护自动化测试和容器部署流程。"),
        ("许知夏", "深圳", "视觉设计师", "本科", "广州美术学院", "视觉传达", ["Figma", "Photoshop", "品牌设计"], "2022-03", "木禾设计", "负责品牌视觉规范、活动物料和数字产品界面设计。"),
        ("周亦舟", "北京", "数据分析师", "硕士", "北京师范大学", "统计学", ["Python", "SQL", "数据分析", "Tableau"], "2021-07", "星河数据", "分析用户留存和转化数据，搭建业务指标体系与月度分析报告。"),
        ("宋清和", "成都", "前端开发工程师", "本科", "四川大学", "软件工程", ["TypeScript", "Vue", "CSS", "Git"], "2023-05", "山岚网络", "开发运营后台与移动端页面，建设通用组件和接口联调流程。"),
        ("顾明澈", "南京", "运营专员", "大专", "南京工业职业技术大学", "市场营销", ["内容运营", "Excel", "活动策划"], "2021", "新禾文化", "策划社群活动与内容专题，整理渠道反馈和月度运营数据。"),
        ("江晚宁", "苏州", "测试工程师", "本科", "苏州大学", "计算机科学", ["Python", "测试用例", "自动化测试", "SQL"], "2020-09", "清源信息", "维护回归测试用例，开发接口自动化脚本并跟踪缺陷闭环。"),
        ("沈星野", "武汉", "应届毕业生", "本科", "武汉理工大学", "电子信息", ["Python", "Excel", "数据分析"], "", "", "完成课程数据分析项目，负责数据清洗与可视化。"),
    ]
    rows = []
    for index in range(count):
        name, city, role, degree, school, major, skills, start, company, summary = templates[index % len(templates)]
        if index >= len(templates):
            name += str(index + 1)
        education = [{"level": degree, "school": school, "degree": "工学学士" if degree == "本科" else degree,
                      "major": major, "college": "", "study_type": "全日制", "graduation_status": "毕业",
                      "start_date": "2015-09", "end_date": "2019-06"}]
        if index % 8 == 1:
            education = [
                {"level": "本科", "school": "四川大学", "college": "计算机学院", "degree": "工学学士", "major": "软件工程", "study_type": "全日制", "start_date": "2012-09", "end_date": "2016-06", "graduation_status": "毕业", "academic_details": "获得校级奖学金"},
                {"level": "硕士", "school": "浙江大学", "college": "计算机科学与技术学院", "degree": "工程硕士", "major": "计算机技术", "study_type": "全日制", "start_date": "2016-09", "end_date": "2019-06", "graduation_status": "毕业", "research_direction": "分布式系统", "academic_details": "论文研究分布式任务调度"}]
        elif index % 8 == 3:
            education = [
                {"level": "本科", "school": "武汉大学", "college": "数学与统计学院", "degree": "理学学士", "major": "数学与应用数学", "study_type": "全日制", "start_date": "2011-09", "end_date": "2015-06", "graduation_status": "毕业"},
                {"level": "硕士", "school": "北京师范大学", "college": "统计学院", "degree": "应用统计硕士", "major": "应用统计", "study_type": "全日制", "start_date": "2015-09", "end_date": "2018-06", "graduation_status": "毕业", "research_direction": "时间序列分析"},
                {"level": "博士", "school": "北京师范大学", "college": "统计学院", "degree": "理学博士", "major": "统计学", "study_type": "全日制", "start_date": "2018-09", "end_date": "2021-06", "graduation_status": "毕业", "research_direction": "统计学习", "academic_details": "博士论文研究高维统计模型"}]
        elif index % 8 == 7:
            education[0].update(start_date="2022-09", end_date="2026-06")
        for item in education:
            item["source_text"] = " ".join(str(value) for key, value in item.items() if value)
        experience = [{"company": company, "title": role, "employment_type": "工作", "start_date": start, "end_date": "至今", "description": summary}] if start else []
        experience.append({"company": "知行科技", "title": "数据分析实习生" if index % 8 == 7 else role + "实习生", "employment_type": "实习", "department": "产品与技术部", "location": city, "start_date": "2025-07" if index % 8 == 7 else "2018-07", "end_date": "2025-12" if index % 8 == 7 else "2018-12", "description": "整理业务数据，协助完成周报和需求验证。", "achievements": "制作6份数据分析周报", "claimed_duration": "6个月"})
        if index % 8 == 1:
            experience[0].update(start_date="2021-01", department="平台研发部", achievements="将批处理耗时降低30%")
            experience.insert(0, {"company": "云帆软件", "title": "初级后端工程师", "employment_type": "工作", "start_date": "2019-06", "end_date": "2020-12", "description": "开发业务接口与单元测试。"})
        for item in experience:
            item["source_text"] = " ".join(str(value) for key, value in item.items() if value)
        data = normalize_candidate({"name": name, "phone": "", "email": f"demo{index + 1}@example.invalid", "location": city,
                                    "target_role": role, "summary": summary, "skills": skills,
                                    "education": education,
                                    "experience": experience, "target_city": city, "job_status": "应届" if index % 8 == 7 else "在职", "availability": "协商到岗",
                                    "projects": [{"name": "业务数据看板", "role": "数据整理与可视化", "start_date": "2024-03", "end_date": "2024-06", "description": "整理指标口径并制作业务看板。", "technologies": "Python、SQL", "achievements": "覆盖3个业务指标"}],
                                    "qualifications": [{"category": "证书", "name": "大学英语六级", "date": "2018-06", "details": "成绩510分"}], "languages": [{"name": "英语", "test_score": "CET-6 510分"}],
                                    "evidence": [{"field": "name", "quote": name}], "notes": []})
        rows.append({"id": uuid.uuid4().hex, "filename": f"演示_{name}.pdf", "status": "done", "data": data,
                     "remark": "已电话沟通，周五14:00面试。\n待确认到岗时间。" if index % 8 == 1 else "",
                     "short_remark": "待确认到岗" if index % 8 == 1 else "",
                     "remark_color": "#a86513" if index % 8 == 1 else "#18594c",
                     "raw_text": f"{name}\n" + "\n".join(item["source_text"] for item in education + experience) + f"\n{summary}\n{'、'.join(skills)}", "method": "演示"})
    return rows
