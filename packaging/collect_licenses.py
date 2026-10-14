"""Include runtime dependency notices in the distributed application."""
from importlib.metadata import distribution
from pathlib import Path


def license_data(root):
    packages = []
    for line in (root / "requirements.lock.txt").read_text(encoding="utf-8-sig").splitlines():
        if "==" in line:
            packages.append(line.split("==", 1)[0])
    data = []
    for package in packages:
        dist = distribution(package)
        for entry in dist.files or []:
            name = entry.name.casefold()
            if "licenses" in entry.parts or name.startswith(("license", "copying", "notice")):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    data.append((str(source), "licenses/" + package))
    return data
