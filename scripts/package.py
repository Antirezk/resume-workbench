from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent.parent
destination = root / "output" / "简历工作台.zip"
destination.parent.mkdir(parents=True, exist_ok=True)
files = [root / name for name in ["app.py", "desktop.py", "start.bat", "start.ps1", "requirements.txt", "requirements.lock.txt", "README.md", "LICENSE", ".gitignore"]]
files.extend(root / "fonts" / name for name in ["NotoSansSC-Regular.ttf", "OFL.txt"])
for folder in ["resume_tool", "templates", "static", "tests", "packaging"]:
    files.extend(path for path in (root / folder).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, "简历工作台/" + path.relative_to(root).as_posix())
    for filename in ["中文简历汇总_演示.pdf", "中文简历整理_演示.xlsx"]:
        archive.write(root / "output" / "verification" / filename, "简历工作台/演示报表/" + filename)
print(destination)
