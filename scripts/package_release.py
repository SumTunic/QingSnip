"""Create the complete portable ZIP, license notices and SHA-256 file."""
from __future__ import annotations

import ast
import hashlib
import json
import platform
import shutil
import sys
import zipfile
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project_version():
    tree = ast.parse((ROOT / "snip" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("snip/__init__.py does not declare __version__")


def add_license_files(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "licenses", target, dirs_exist_ok=True)
    records = []
    for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "uiautomation", "comtypes", "pyinstaller"):
        package = distribution(name)
        copied = []
        for entry in package.files or []:
            parts = [part.lower() for part in entry.parts]
            if "licenses" not in parts and not any(word in entry.name.lower() for word in ("license", "copying", "notice")):
                continue
            original = Path(package.locate_file(entry))
            if original.is_file():
                relative = Path(name) / entry.name
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, destination)
                copied.append(relative.as_posix())
        records.append({"name": name, "version": package.version, "license": package.metadata.get("License-Expression") or package.metadata.get("License"), "files": copied})
    python_license = Path(sys.base_prefix) / "LICENSE_PYTHON.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Cannot find the Python runtime license")
    destination = target / "Python" / "LICENSE.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, destination)
    records.append({"name": "Python", "version": platform.python_version(), "files": ["Python/LICENSE.txt"]})

    # Conda-based builds can bring additional native DLLs. Retain the license
    # files available in their package records without bundling package caches.
    native_names = {file.name.lower() for file in (ROOT / "dist" / "QingSnip" / "_internal").glob("*.dll")}
    for metadata_path in (Path(sys.base_prefix) / "conda-meta").glob("*.json"):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not any(Path(file).name.lower() in native_names for file in metadata.get("files", [])):
            continue
        original = Path(metadata.get("link", {}).get("source", "")) / "info" / "licenses"
        copied = []
        for file in original.rglob("*") if original.is_dir() else []:
            if file.is_file():
                relative = Path("native") / metadata["name"] / file.relative_to(original)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, destination)
                copied.append(relative.as_posix())
        if not copied and metadata["name"] == "libmpdec":
            copied = ["mpdecimal/COPYRIGHT.txt", "mpdecimal/doc-COPYRIGHT.txt"]
        if not copied and metadata["name"] == "python":
            copied = ["Python/LICENSE.txt"]
        records.append({"name": metadata["name"], "version": metadata["version"], "license": metadata.get("license"), "files": copied})
    (target / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    from PyInstaller.archive.readers import CArchiveReader

    version = project_version()
    folder = ROOT / "dist" / "QingSnip"
    executable = folder / "QingSnip.exe"
    if not executable.is_file() or not (folder / "_internal" / "PySide6" / "QtCore.pyd").is_file():
        raise RuntimeError("Run build.bat before creating a release archive")
    archive = CArchiveReader(executable)
    embedded = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith(".pyz")))
    if version not in embedded.extract("snip").co_consts:
        raise RuntimeError("Executable version does not match source version; rebuild first")

    output = ROOT / ".release"
    output.mkdir(exist_ok=True)
    notice_folder = output / "notices"
    add_license_files(notice_folder)
    filename = f"QingSnip-v{version}-windows-x64.zip"
    target = output / filename
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for file in sorted(folder.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts and file.suffix.lower() != ".log":
                package.write(file, (Path("QingSnip") / file.relative_to(folder)).as_posix())
        for file in sorted(notice_folder.rglob("*")):
            if file.is_file():
                package.write(file, (Path("QingSnip/licenses") / file.relative_to(notice_folder)).as_posix())
        for name in ("README.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md"):
            package.write(ROOT / name, "QingSnip/" + name)
        package.writestr("QingSnip/使用说明.txt", "轻截 QingSnip\n\n请先完整解压 ZIP，再运行 QingSnip.exe。\nQingSnip.exe 和 _internal 文件夹必须放在一起。\n默认快捷键 Ctrl+Shift+A，完成截图后在目标软件按 Ctrl+V。\n关闭设置窗口后常驻系统托盘，右键托盘图标可退出。\n")
    with zipfile.ZipFile(target) as package:
        failure = package.testzip()
        if failure:
            raise RuntimeError(f"Archive CRC check failed: {failure}")
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {filename}\n", encoding="ascii")
    print(f"Archive: {target.name} ({target.stat().st_size:,} bytes)")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
