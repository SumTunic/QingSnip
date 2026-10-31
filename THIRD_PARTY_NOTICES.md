# 第三方组件声明

轻截的打包版本包含第三方运行库。这些组件保留各自的版权与许可，项目源码的发布不改变第三方组件的许可。

| 组件 | 来源及许可信息 |
| --- | --- |
| Python | [CPython](https://www.python.org/)，随包保留 Python 许可文本 |
| Qt / PySide6 / Shiboken6 | [Qt for Python](https://doc.qt.io/qtforpython-6/)，上游提供 LGPL/GPL/商业许可选项；随包保留相关许可文本 |
| uiautomation | [Python UIAutomation for Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)，Apache 2.0 |
| comtypes | [comtypes](https://github.com/enthought/comtypes)，MIT |
| PyInstaller 引导程序 | [PyInstaller](https://pyinstaller.org/)，GPL 及其引导程序分发例外，见随包 COPYING.txt |

发布 ZIP 的 `licenses/manifest.json` 列出构建时组件版本和收集到的许可文件。`licenses/` 还保留当前构建环境可提供的 bzip2、libffi、zlib、OpenSSL、xz 和 Microsoft 运行库许可。

Qt 许可信息与第三方组件说明：

- <https://doc.qt.io/qt-6/licensing.html>
- <https://doc.qt.io/qt-6/licenses-used-in-qt.html>
- <https://doc.qt.io/qtforpython-6/licenses.html>
- Qt 源码：<https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.12.0>
- PySide6 源码：<https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0>

仓库 `licenses/Qt/` 中的 LGPL/GPL 文本来自 Qt 官方 `qtbase` 的 `v6.12.0` 标签。`licenses/mpdecimal/` 的声明来自上游 `mpdecimal-4.0.0` 发布归档，适用于当前构建所带的 mpdecimal 运行库。
