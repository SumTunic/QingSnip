# 轻截 QingSnip

Windows 托盘截图工具：快捷键唤起，框选与标注，完成后复制到剪贴板。

[下载最新 Windows 版本](https://github.com/SumTunic/QingSnip/releases/latest) · [版本记录](CHANGELOG.md)

## 下载与运行

1. 在 [Releases](https://github.com/SumTunic/QingSnip/releases) 下载 `QingSnip-v版本号-windows-x64.zip`。
2. **完整解压** ZIP，进入 `QingSnip` 文件夹，双击 `QingSnip.exe`。无需安装 Python。
3. 保留同目录的 `_internal` 文件夹，移动程序时请移动整个 `QingSnip` 文件夹。

默认截图快捷键是 **Ctrl+Shift+A**。关闭设置窗口后，程序留在系统托盘；单击托盘图标截图，右键打开设置或退出。

目标平台为 Windows 10/11 x64，当前已在 Windows 11 x64 上测试。Release 同时提供 `SHA256SUMS.txt`，可用 PowerShell 核对下载文件：

```powershell
Get-FileHash .\QingSnip-v0.4.2-windows-x64.zip -Algorithm SHA256
```

## 功能

- 自定义键盘快捷键、PrintScreen、鼠标中键或侧键组合。
- 智能窗口/内部区域候选、滚轮切换、Alt 整窗选择及手动框选。
- 矩形、椭圆、箭头、画笔、文字和马赛克标注。
- 文字可移动、缩放、旋转、修改字号与颜色，双击重新编辑。
- 马赛克画笔支持像素块、模糊和实色遮盖，光标随背景调整对比。
- 完成截图即复制，支持单次 PNG 保存、可选自动保存和开机启动。
- 绘制、文字编辑与选区调整均可撤销/重做。

## 截图操作

| 操作 | 方式 |
| --- | --- |
| 选择区域 | 单击智能选区，或按住左键拖动 |
| 切换候选区域 | 鼠标滚轮 |
| 选择整窗 | 按住 Alt |
| 移动 / 调整选区 | 拖动选区 / 边缘控制点 |
| 完成并复制 | Enter / “完成” |
| 取消截图 | Esc / 鼠标右键（可关闭） |
| 单次保存 PNG | Ctrl+S / “保存…” |
| 撤销 / 重做 | Ctrl+Z / Ctrl+Y |

完成后到目标软件按 **Ctrl+V** 粘贴。

粗细通过圆点选择。马赛克拖动涂抹，单击也可留下圆形涂抹，每笔按一次操作撤销。文字输入时 Enter 确认、Shift+Enter 换行、Esc 放弃当前输入；确认后单击选中，拖文字移动、拖四角缩放、拖上方圆点旋转，双击重新输入。

## 设置与数据位置

界面分为“快捷键”“常规”和“图片保存”三组：

- **键盘、鼠标快捷键**：修改后点击旁边的“应用快捷键”。冲突时保留原来的快捷键。
- **开机自动启动**：开关立即生效，当前用户下次登录后在托盘后台运行。
- **智能识别区域、右键取消、截图时隐藏窗口**：开关立即生效；关闭隐藏窗口即可截取轻截自身。
- **自动保存 PNG**：默认关闭；开启后完成截图时同时保存到所选文件夹。
- **保存目录**：选择文件夹或结束输入后自动生效；可直接点击“打开文件夹”。

配置和日志默认位于 `%APPDATA%\QingSnip\`，可用 `--config 路径\settings.json` 指定配置位置。开机启动保存绝对程序路径，移动程序后请重新关闭并开启该选项。

## 从源码运行

需要 Windows 和 Python 3.11+。双击 `setup.bat` 可创建环境和安装运行依赖；也可手动执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py --settings
```

`start.bat` 优先启动本地 `dist\QingSnip\QingSnip.exe`，不存在打包程序时才使用源码环境。需要运行修改后的源码时，请使用上面的 `main.py` 命令。

## 测试、构建与发布包

构建前请先从系统托盘退出已运行的轻截，释放程序文件。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm QingSnip.spec
.\.venv\Scripts\python.exe scripts\package_release.py
```

也可运行 `build.bat`，它会安装开发依赖、执行测试并生成 `dist\QingSnip\`。发布脚本校验源码与 EXE 版本，生成包含完整运行目录、说明与第三方许可的 ZIP，以及 `.release\SHA256SUMS.txt`。

Windows 原生窗口层级检查：

```powershell
.\.venv\Scripts\python.exe -c "import runpy; runpy.run_path('tests/native_window_smoke.py', run_name='__main__')"
```

该检查会短暂显示测试遮罩，验证标题、工具栏层级、文字焦点和撤销/重做，不注入桌面键鼠输入。

## 目录

```text
snip/                  应用源码
assets/                程序图标
scripts/               Release 打包脚本
tests/                 自动测试与 Windows 窗口检查
licenses/              随发布包保留的第三方许可文本
QingSnip.spec          PyInstaller 构建配置
requirements*.txt      运行与开发依赖
CHANGELOG.md           版本记录
THIRD_PARTY_NOTICES.md  第三方组件说明
```

`dist/`、`build/`、`.release/`、虚拟环境、测试缓存、日志和本地设置均由 `.gitignore` 排除，不上传到源码仓库。可执行程序通过 GitHub Releases 分发。

## 已知范围

内部区域识别依赖应用公开的窗口与辅助功能边界，无法保证识别所有自绘界面。混合 DPI 多屏、实际开机登录启动与特定应用的内部识别仍需对应设备验收。安全桌面和受保护画面受 Windows 捕获机制限制。

第三方组件及许可来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
