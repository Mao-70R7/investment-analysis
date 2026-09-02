from __future__ import annotations

import re
from pathlib import Path


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
PROGRAM_ROOT = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序"
MARKER = 'next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())'
PROGRAM_RELATIVE_WINDOWS = r"节点脚本\_共享组件\生产程序"


def write_text(path: Path, text: str) -> None:
    encoding = "utf-8-sig" if path.suffix.lower() == ".ps1" else "utf-8"
    path.write_text(text, encoding=encoding)


def migrate_python(path: Path) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    text = re.sub(r"Path\(__file__\)\.resolve\(\)\.parents\[\d+\]", MARKER, original)
    text = re.sub(r"Path\(__file__\)\.resolve\(\)\.parent\.parent", MARKER, text)
    text = re.sub(r"(?m)^(PROJECT_ROOT|ROOT)\s*=\s*SCRIPT_DIR\.parent\s*$", rf"\1 = {MARKER}", text)
    text = text.replace('PROJECT_ROOT / "scripts"', 'PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"')
    text = text.replace("PROJECT_ROOT / 'scripts'", "PROJECT_ROOT / '节点脚本' / '_共享组件' / '生产程序'")
    text = text.replace('ROOT / "scripts"', 'ROOT / "节点脚本" / "_共享组件" / "生产程序"')
    text = text.replace("ROOT / 'scripts'", "ROOT / '节点脚本' / '_共享组件' / '生产程序'")
    text = text.replace('PROJECT_ROOT / "src"', 'PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"')
    text = text.replace("PROJECT_ROOT / 'src'", "PROJECT_ROOT / '节点脚本' / '_共享组件' / 'python_src'")
    text = text.replace('ROOT / "src"', 'ROOT / "节点脚本" / "_共享组件" / "python_src"')
    if text == original:
        return False
    write_text(path, text)
    return True


def migrate_powershell(path: Path) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    root_expression = (
        "$ProjectRoot = if ($env:ADVISOR_CODE_ROOT) { "
        "[System.IO.Path]::GetFullPath($env:ADVISOR_CODE_ROOT) } else { "
        "(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\\..\\..')).Path }"
    )
    text = re.sub(r"(?m)^\$ProjectRoot\s*=\s*Split-Path -Parent \$PSScriptRoot\s*$", root_expression, original)
    text = text.replace(".\\scripts\\", ".\\" + PROGRAM_RELATIVE_WINDOWS + "\\")
    text = text.replace('Join-Path $ProjectRoot "scripts\\', 'Join-Path $ProjectRoot "' + PROGRAM_RELATIVE_WINDOWS + "\\")
    text = text.replace("Join-Path $ProjectRoot 'scripts\\", "Join-Path $ProjectRoot '" + PROGRAM_RELATIVE_WINDOWS + "\\")
    if text == original:
        return False
    write_text(path, text)
    return True


def migrate_batch(path: Path) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    text = original.replace("%PROJECT_ROOT%\\scripts\\", "%PROJECT_ROOT%\\" + PROGRAM_RELATIVE_WINDOWS + "\\")
    text = text.replace("%MONITOR_ROOT%\\scripts\\", "%MONITOR_ROOT%\\" + PROGRAM_RELATIVE_WINDOWS + "\\")
    text = text.replace(".\\scripts\\", ".\\" + PROGRAM_RELATIVE_WINDOWS + "\\")
    if text == original:
        return False
    write_text(path, text)
    return True


def main() -> None:
    counters = {"python": 0, "powershell": 0, "batch": 0}
    for path in PROGRAM_ROOT.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() == ".py" and migrate_python(path):
            counters["python"] += 1
        elif path.suffix.lower() == ".ps1" and migrate_powershell(path):
            counters["powershell"] += 1
        elif path.suffix.lower() in {".bat", ".cmd"} and migrate_batch(path):
            counters["batch"] += 1
    print(counters)


if __name__ == "__main__":
    main()
