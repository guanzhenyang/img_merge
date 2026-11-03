# 图片工坊

离线小工具：把图片或整个文件夹导进来，在左边看缩略图和大图，在右边改文件名、等比缩放、旋转和翻转，再把结果写到另一个文件夹。原始文件保持原样。

不需要账号，核心功能也不访问网络。

## 功能

- 添加一个或多个图片文件，或添加文件夹（包含子文件夹里的图片）。也可以把文件和文件夹直接拖进窗口。
- 左边是缩略图列表和大预览，右边是当前图片的编辑。
- 重命名时可以改主文件名，也可以改扩展名。扩展名会决定导出格式。
- 等比缩放：默认锁定宽高比，可以填宽度、高度，或拖动缩放百分比。
- 旋转：逆时针 90°、顺时针 90°、180°。可以水平翻转、垂直翻转。
- 预览会立刻反映这些修改。点「应用到当前图片」后，列表里的名字和「已改」标记才更新。
- 「把缩放和旋转应用到全部」只复制旋转、翻转和缩放规则，不改每张图自己的文件名。宽度或百分比会按每张图自己的尺寸计算。
- 批量分辨率有两种模式。默认是「拉伸到精确尺寸」：填写宽和高（例如 900×600），每张图都拉成这个像素。另一种是「按宽度等比」：只填宽度（例如 900），高度按每张图旋转后的宽高比计算。可以「应用到选中」或「应用到全部」。按住 Ctrl（Mac 上用 Command）或 Shift 点左侧列表可多选；Ctrl+A 选中全部。批量操作不改文件名、旋转和翻转。
- 导出到选定文件夹。默认不覆盖已有文件，同名时自动加 `_1`、`_2`。勾选覆盖后，也只覆盖输出文件夹里的文件，不会改原图。
- 导出结束时显示成功张数和失败张数。

## 支持的格式

导入：`jpg`、`jpeg`、`jpe`、`jfif`、`png`、`webp`、`bmp`、`gif`、`tif`、`tiff`。

导出可选：`jpg`、`png`、`webp`、`bmp`、`gif`、`tiff`。原来就是 `jpeg`、`jfif` 或 `tif` 的文件可以保持原扩展名。

HEIC、RAW 不在这个列表里。请先用其他工具转成 JPG 或 PNG。

处理顺序是：按 EXIF 方向摆正，再顺时针旋转，再翻转，最后缩放。动图预览用第一帧。导出为 GIF 或 WebP 时会尽量保留每一帧；导出成 JPG、PNG、BMP、TIFF 时只保留第一帧，并在结果里说明。

只改文件名、扩展名仍属于同一类（例如 `jpeg` 改成 `jpg`），且没有旋转、翻转或缩放时，程序直接复制文件，避免 JPEG 被再压一次。真正改了像素或格式时才会重新编码。JPG 质量 90，WebP 质量 90。透明图存成 JPG 或 BMP 时，透明区域铺成白色。

## 环境

- Python 3.11 或更高版本
- Windows、macOS 或 Linux 都可以运行源码
- Windows 安装包请在 Windows 上打包。从 python.org 安装时勾选 **tcl/tk** 和 **pip**

依赖只有三样：CustomTkinter、Pillow、tkinterdnd2。

## 运行

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

也可以双击 `run.bat`（Windows）或执行 `./run.sh`（macOS / Linux）。脚本会优先使用项目里的 `.venv`。

Linux 需要系统里已有 Tk，例如 Ubuntu 的 `python3-tk`。

### 快捷键

| 快捷键 | 作用 |
| --- | --- |
| Ctrl+O | 添加文件 |
| Ctrl+Shift+O | 添加文件夹 |
| Ctrl+Enter | 应用到当前图片 |
| Ctrl+E | 导出全部 |
| ↑ / ↓ | 切换图片 |
| Delete | 从列表移出（不删除磁盘文件） |
| Ctrl+A | 选中列表中的全部图片 |
| Ctrl / Command + 点击 | 加减选中 |
| Shift + 点击 | 按范围选中 |

## 打包 Windows 程序

在 **Windows** 上，于项目目录执行：

```bat
build_windows.bat
```

或在 PowerShell 里：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
```

脚本会安装 `requirements-build.txt`（含 PyInstaller），再按 `image_workbench.spec` 打包。

生成结果：

```text
dist\图片工坊\图片工坊.exe
```

这是单目录模式。请把整个 `dist\图片工坊` 文件夹一起复制到别处，里面的 `_internal` 是程序运行需要的库和拖放组件。

想打成单个 exe 时，把 `image_workbench.spec` 顶部的 `one_file = False` 改成 `one_file = True`，再执行打包脚本。单个 exe 第一次启动会慢一些。

打包必须在目标系统上进行。这台开发环境如果是 Linux，不能可靠地做出能在 Windows 上双击运行的 exe：PyInstaller 打进去的是当前系统的 Python，拖放库也只带当前系统的文件。请在 Windows 上运行上面的脚本。

如果窗口一闪而过，把 spec 里的 `console=False` 临时改成 `console=True` 重新打包，用命令行里的报错排查。正常使用保持 `console=False`。运行日志里的意外错误会写到：

- Windows：`%APPDATA%\image-workbench\error.log`
- Linux：`~/.config/image-workbench/error.log`
- macOS：`~/Library/Application Support/image-workbench/error.log`

## 打包 macOS 应用

Mac 应用必须在 **macOS** 上打包。这台环境如果是 Linux，不能生成可双击的 `.app`，也不能做 Apple 签名或公证。脚本和说明已经放在仓库里，请把源码拷到 Mac 后执行：

```bash
chmod +x build_macos.sh
./build_macos.sh
```

脚本会用系统自带的 `sips` 和 `iconutil` 从 `assets/icon.png` 生成 `assets/icon.icns`，安装 PyInstaller，再按 `image_workbench.spec` 打出：

```text
dist/图片工坊.app
```

这是未签名的应用。第一次打开若被系统拦住，右键点应用选「打开」，或在终端执行：

```bash
xattr -dr com.apple.quarantine "dist/图片工坊.app"
```

需要单个可执行文件而不是 `.app` 目录结构时，把 `image_workbench.spec` 里的 `one_file = False` 改成 `True` 再运行脚本。默认的 `.app` 更适合拖到「应用程序」文件夹。

### 用 GitHub Actions 打包

仓库里的 `.github/workflows/build-macos.yml` 会在 GitHub 的 **Intel** 托管机 `macos-15-intel`（x86_64）上安装 Python 3.12，执行 `build_macos.sh`，并把打好的应用做成 zip 上传为 Actions 产物。当前 Actions 产物是 **Intel Mac（x86_64）** 专用；Apple Silicon（M 系列）若需要，应另打 arm64 包。应用没有签名，也没有公证。Windows 的 `build_windows.bat` / `build_windows.ps1` 不变，这个工作流不负责 Windows exe。

如果代码还只在 Origin 草稿里，GitHub 不会跑这个工作流。先在 GitHub 建一个空仓库（不要勾选自动生成 README，免得和现有历史冲突），再把 `main` 推上去：

```bash
git remote add github https://github.com/<你的用户名>/<仓库名>.git
git push -u github main
```

远端名字 `github` 如果已经被占用，换一个名字，或直接 `git push https://github.com/<你的用户名>/<仓库名>.git main`。推送时带上工作流文件，这次推送到 `main` 就会开始构建。

之后有两种触发方式：

- 再次把提交推到 `main`，工作流自动跑。
- 打开仓库的 **Actions**，左侧选 **Build macOS app**，点 **Run workflow**，分支选 `main`。手动按钮要等工作流文件已经在默认分支上才会出现。

构建成功后，打开那次运行，在 **Artifacts** 里下载 `image-workbench-macos`。GitHub 会再包一层 zip。解压后看到 `图片工坊-macos.zip`，再解压一次得到 `图片工坊.app`。该产物为 **Intel x86_64**，适用于 Intel 处理器的 Mac；产物保留 30 天。

Gatekeeper 会拦住从网络下载的未签名应用。把 `.app` 拖进「应用程序」后，按住 Control 点它，选「打开」，在对话框里再确认一次。若仍然提示已损坏或无法打开，在终端执行：

```bash
xattr -dr com.apple.quarantine "/Applications/图片工坊.app"
```

路径按你实际放置 `.app` 的位置改。

## 自测

```bash
python -m unittest discover -s tests
```

## 目录

```text
run.py                  启动入口
app/                    界面、图像处理和导出
assets/                 图标和界面主题
image_workbench.spec    PyInstaller 配置
build_windows.bat       Windows 打包
build_windows.ps1       Windows 打包（PowerShell）
build_macos.sh          macOS 本地打包（生成 图片工坊.app）
.github/workflows/build-macos.yml
                        GitHub Actions：macos-15-intel（x86_64）上打包并上传 zip
requirements.txt        运行依赖
requirements-build.txt  打包时额外需要的 PyInstaller
```
