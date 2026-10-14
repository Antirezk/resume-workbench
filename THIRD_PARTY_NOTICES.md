# 第三方组件、模型与参考来源

维护者：Kyle。应用代码采用 [MIT License](LICENSE)。MIT 不替代下列第三方资产自己的许可证。

## 来源关系

本项目采用开源组件的公开接口实现中文简历工作台。应用层提供批次管理、结构化字段与核对逻辑、工作/实习时长计算、SQLite 候选人数据库、备注、网页和报表。

当前发布不以某个完整简历应用项目的 fork 为基础仓库。这里标注的项目主要是运行依赖、打包工具、模型/字体来源及接口参考，不能据此声称其代码或模型由本项目原创。若后续直接复制或修改上游代码，应逐项补充来源文件、版本/提交、修改说明及上游要求的版权声明。

## 主要运行组件

版本清单见 [requirements.lock.txt](requirements.lock.txt)，包括间接依赖。下表说明主要组件用途；二进制发行中的许可原文和具体组件版本为准。

| 组件与上游来源 | 用途 | 许可证/说明 |
| --- | --- | --- |
| [Flask](https://github.com/pallets/flask)、[Jinja2](https://github.com/pallets/jinja)、[Werkzeug](https://github.com/pallets/werkzeug) | 本地网页服务、模板与请求处理 | BSD-3-Clause |
| [Waitress](https://github.com/Pylons/waitress) | Windows EXE 内的 HTTP 服务 | ZPL-2.1 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | 中文扫描件 OCR | Apache-2.0；模型另需保留上游归属 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 本地 OCR 模型推理 | MIT，附带依赖保留其许可 |
| [OpenCV](https://github.com/opencv/opencv)、[opencv-python](https://github.com/opencv/opencv-python) | OCR 图像处理 | 核心及 Python 分发包的许可分别以上游声明为准 |
| [NumPy](https://github.com/numpy/numpy) | OCR 数组处理 | BSD-3-Clause，附带依赖保留其许可 |
| [pypdf](https://github.com/py-pdf/pypdf) | PDF 文字读取 | BSD-3-Clause |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)、[PDFium](https://pdfium.googlesource.com/pdfium/) | PDF 页面渲染供 OCR 使用 | pypdfium2 提供 Apache-2.0 / BSD-3-Clause 选择；PDFium 及其依赖保留各自许可 |
| [python-docx](https://github.com/python-openxml/python-docx)、[lxml](https://github.com/lxml/lxml) | DOCX 文件读取 | MIT / BSD，lxml 附带的底层库保留其许可 |
| [ReportLab](https://www.reportlab.com/opensource/) | 中文 PDF 汇总报告 | BSD 类许可，以上游许可原文为准 |
| [Pillow](https://github.com/python-pillow/Pillow) | 图像与托盘图标处理 | HPND 及附带第三方许可 |
| [requests](https://github.com/psf/requests) | 模型 API HTTP 请求 | Apache-2.0 |
| [pystray](https://github.com/moses-palmer/pystray) | Windows 系统托盘 | LGPL-3.0，保留上游许可 |
| [Python](https://www.python.org/) / 标准库 SQLite 接口 | 应用运行与本地存储 | Python 软件及附带组件保留各自许可 |

Excel 导出由应用代码生成 OOXML；不依赖用户安装 Microsoft Excel。

## OCR 模型

Windows 发行包包含通过 RapidOCR 分发的 ONNX OCR 模型，测试包中的文件名称为：

- `PP-OCRv6_det_small.onnx`
- `PP-OCRv6_rec_small.onnx`
- `ch_ppocr_mobile_v2.0_cls_mobile.onnx`

模型不是本项目训练的资产。RapidOCR 的模型来源关系涉及 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)，转换与分发说明应遵循 [RapidOCR 模型许可说明](https://github.com/RapidAI/RapidOCR/blob/main/MODEL_LICENSES.md) 和所使用具体模型的上游声明。模型权利属于相应上游权利人，不因本项目应用代码使用 MIT 而改变。

## 中文字体

内置 `fonts/NotoSansSC-Regular.ttf` 来自 Noto CJK / Source Han Sans 字体体系，用于中文 PDF。上游：[Noto CJK](https://github.com/notofonts/noto-cjk)。采用 SIL Open Font License 1.1，完整版权与许可原文保存在 [fonts/OFL.txt](fonts/OFL.txt)。发行包不得删除该声明。

## 打包工具与许可保留

使用 [PyInstaller](https://github.com/pyinstaller/pyinstaller) 生成 Windows EXE。PyInstaller 包含 GPL 许可与允许分发打包应用的例外，具体见 [官方许可说明](https://pyinstaller.org/en/stable/license.html)。

[packaging/collect_licenses.py](packaging/collect_licenses.py) 收集锁定运行依赖安装包中的 LICENSE、COPYING、NOTICE 等文件；[packaging/windows.spec](packaging/windows.spec) 保留这些文件、字体 OFL 与应用 LICENSE。用户可从 EXE 托盘“开源组件许可”打开内置许可目录。该收集过程不改变上游权利，也不能替代对新增或更换模型/原生库的许可核对。

## 接口文档参考

- [RapidOCR 官方文档](https://rapidai.github.io/RapidOCRDocs/)：本地 OCR 接口与模型配置。
- [通义千问兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)：API 参数与响应格式。
- [通义千问结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)：JSON 输出设置。
- [DeepSeek API 文档](https://api-docs.deepseek.com/)：兼容服务配置。

API 服务本身不随仓库提供。需要使用者自行配置服务商账号、API Key 和模型；密钥不写入仓库或候选人数据库。
