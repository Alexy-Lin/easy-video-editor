# Third-party notices / 第三方声明

This file applies to the Windows portable release. The Easy Video Editor application source remains licensed under Apache-2.0 as stated in `LICENSE`. Bundled third-party components keep their own licenses. License texts are included in this directory's `licenses/` folder or at the referenced path below.

本文件适用于 Windows 便携发行包。Easy Video Editor 应用源码仍按 `LICENSE` 中的 Apache-2.0 许可证授权。随包第三方组件继续适用各自许可证；许可证文本位于本目录的 `licenses/` 文件夹或下述路径。

## Components / 组件

| Component / 组件 | Version / 版本 | License / 许可证 | Notice and source / 声明与源码 |
| --- | --- | --- | --- |
| PySide6, shiboken6, Qt for Python and Qt / Qt for Python 与 Qt | 6.11.2 | LGPL-3.0 terms used for this release / 本发行包按 LGPL-3.0 条款提供 | `licenses/LGPL-3.0.txt`; [Qt for Python licensing](https://doc.qt.io/qtforpython-6/commercial/index.html), [Qt for Python third-party licenses](https://doc.qt.io/qtforpython-6/licenses.html), [PySide source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=6.11.2), and [Qt source repositories](https://code.qt.io/) |
| CPython runtime / CPython 运行时 | 3.13.14 | Python Software Foundation License 2 / Python 软件基金会许可证 2 | `licenses/Python-LICENSE.txt`; [Python source](https://github.com/python/cpython/tree/v3.13.14) |
| PyInstaller bootloader / PyInstaller 引导程序 | 6.22.3 | GPL-2.0-or-later with Bootloader Exception / GPL-2.0-or-later，含引导程序例外 | `licenses/PyInstaller-COPYING.txt`; [PyInstaller source](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) |
| FFmpeg and FFprobe / FFmpeg 与 FFprobe | 9.0.2 Essentials build | GPL-3.0 | `tools/FFmpeg-LICENSE.txt` and `tools/FFmpeg-README.txt`; [FFmpeg source commit](https://github.com/FFmpeg/FFmpeg/commit/946fcce07b) |
| OpenSSL runtime libraries / OpenSSL 运行库 | 3.0.21 and 3.6.4 | Apache-2.0 | See `LICENSE` and [OpenSSL source](https://github.com/openssl/openssl) |
| Microsoft Visual C++ runtime / Microsoft Visual C++ 运行库 | bundled redistributable DLLs / 随包可再发行 DLL | Microsoft Visual Studio license terms / Microsoft Visual Studio 许可条款 | [Microsoft license terms](https://visualstudio.microsoft.com/license-terms/) |

The release is an onedir package: Qt libraries remain separate files under `_internal`, rather than being merged into the application executable. The matching Easy Video Editor source is provided in the release's source archive so recipients can rebuild the application with modified compatible Qt libraries. Qt for Python may contain other third-party code; consult Qt's third-party license list linked above for the applicable component notices.

发行包采用 onedir 目录结构，Qt 库作为 `_internal` 下的独立文件提供，不与应用可执行文件合并。Release 同时提供对应版本的 Easy Video Editor 源码归档，便于接收者使用修改后的兼容 Qt 库重新构建应用。Qt for Python 可能包含其他第三方代码；具体组件声明请参见上方 Qt 第三方许可证列表。
