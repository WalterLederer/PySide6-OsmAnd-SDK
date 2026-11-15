> **Notice:** This repository was created solely for pull requests and testing purposes. It is not intended for general use, and should not be used as an active or production repository. Please refer to the official repository for the maintained version.
>
>
> # 🗺️ PySide6-OsmAnd-SDK
> Bring OsmAnd's native offline map stack into modern Qt6 and PySide6 apps — with native widgets, `.obf` rendering, and cross-platform build tooling.

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Language](https://img.shields.io/badge/language-Python%203.10%2B-blue)
![Framework](https://img.shields.io/badge/framework-PySide6%20(Qt6)-orange)
![Native](https://img.shields.io/badge/native-C%2B%2B%20%7C%20OpenGL-informational)
![Map Data](https://img.shields.io/badge/map%20data-OsmAnd%20.obf-brightgreen)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-green)
[![GitHub Repo](https://img.shields.io/badge/github-PySide6--OsmAnd--SDK-181717?logo=github)](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK)

---

## ☕ Support

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support%20Development-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/oliverzhao)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=AJKMJMQA8YHPN)

---

## Demo Video

[Demo Video](https://github.com/user-attachments/assets/d2352ee7-398d-4c90-bd96-d747a6b5e0bb)

---

## 🚀 Quick Start

### 1. Clone and prepare Git LFS data

```bash
git lfs install
git lfs pull
```

The bundled `World_basemap_2.obf` demo map is stored through Git LFS. If the file is only about 100 bytes and starts with `version https://git-lfs.github.com/spec/v1`, it is still a pointer file and must be pulled with Git LFS before the preview can render real map data.

### 2. Install Python dependencies

```bash
python -m pip install -e .
```

### 3. Build the native runtime

#### Windows

```powershell
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_helper.ps1
```

or build the native widget with MSVC:

```powershell
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_native_widget_msvc.ps1
```

#### Linux

```bash
bash tools/osmand_render_helper_native/build_linux.sh
```

#### macOS

```bash
QT_ROOT=/opt/homebrew/opt/qt JOBS=4 bash tools/osmand_render_helper_native/build_macos.sh
```

### 4. Launch the preview

```bash
osmand-preview --backend auto
```

You can also run the entry point directly:

```bash
python -m maps.main --backend auto
```

---

## 🌟 Overview

**PySide6-OsmAnd-SDK** is an independent GitHub project for embedding OsmAnd's native map stack into modern Qt6 and PySide6 workflows.

It combines vendored OsmAnd core sources, native build tooling, Python integration, a Qt widget bridge, and a runnable preview application in one repository. The goal is to make offline map rendering easier to build, test, and embed from a single codebase.

Key highlights:

- 🧭 Ports the OsmAnd Qt5-era native map stack to Qt6.
- 🧩 Exposes a native C++ map widget for PySide6 applications.
- 🗺️ Supports offline rendering from OsmAnd `.obf` map data.
- ⚡ Provides both native OpenGL preview and Python-driven raster preview paths.
- 🛠 Ships build scripts for Windows, Linux, and macOS toolchains.
- 📦 Bundles demo map data and OsmAnd resources for local testing.

---

## 🧭 Why This Project Matters

This SDK is designed for developers who want OsmAnd's offline map engine while building with current-generation Qt and Python tooling. It reduces the friction of combining native C++ rendering with a PySide6 user interface, making it a practical foundation for desktop map viewers, GIS tools, travel applications, embedded map previews, and other offline-first location products.

By moving the native stack forward to Qt6 and validating PySide6 compatibility, the project helps OsmAnd-based development stay usable outside older Qt5-only integration paths.

---

## ✨ Key Capabilities

| Capability | Description |
| --- | --- |
| Native widget hosting | Embed the OsmAnd native widget through PySide6 using `.dll`, `.so`, or `.dylib` runtimes. |
| Offline `.obf` rendering | Render OsmAnd binary map packages locally without depending on online map tiles. |
| Python raster path | Use helper-backed Python rendering for preview and integration workflows. |
| OpenGL preview | Test native OpenGL rendering paths through the included preview application. |
| Cross-platform builds | Build helper and widget outputs on Windows, Linux, and macOS. |
| Demo data included | Use the bundled `World_basemap_2.obf` and OsmAnd resources for local validation. |

---

## 🧱 Repository Layout

| Path | Purpose |
| --- | --- |
| `vendor/osmand/build` | Build tree and supporting files used by the Windows-oriented OsmAnd build flow. |
| `vendor/osmand/core` | Vendored OsmAnd core sources. |
| `vendor/osmand/core-legacy` | Vendored legacy core sources still required by the native build. |
| `vendor/osmand/resources` | OsmAnd rendering resources, styles, and data files. |
| `tools/osmand_render_helper_native` | Native helper and widget sources plus Windows, Linux, and macOS build scripts. |
| `src/maps` | PySide6 preview application and Python integration layer. |
| `src/maps/main.py` | Preview entry point. |

---

## 🗺 Map Data and Styles

### `.obf` files

OsmAnd `.obf` files are offline binary map packages. They store the geographic content consumed by the native engine, including roads, boundaries, land use, water, place labels, routing-related data, and points of interest.

This repository's preview defaults to:

```text
src/maps/tiles/World_basemap_2.obf
```

The bundled `.obf` is only a default demo dataset. You can replace it with another OsmAnd `.obf` file, select a different file from the preview window, or point the runtime to a custom path through the documented environment variables.

Additional `.obf` map downloads are available from the official OsmAnd download list:

```text
https://download.osmand.net/list.php
```

When a local `plugin/data/geonames.sqlite3` GeoNames index is present, the preview search demo prefers it for place-name lookup and uses the active `.obf` search path as a fallback. The optimized database is compatible with the `GeoNames-search` project output, uses the compact `cities500`-based schema, and keeps global place-name queries such as `北京` / `Beijing` responsive without scanning the full database in Python.

### Rendering styles

Rendering styles are XML-based rule files under:

```text
vendor/osmand/resources/rendering_styles
```

Common examples include:

- `default.render.xml`
- `mapnik.render.xml`
- `snowmobile.render.xml`

These files control how the same `.obf` data appears on screen: colors, line rules, polygon fills, icons, labels, and theme-specific display logic. The Python integration layer passes both the selected `.obf` data source and active style file into the OsmAnd rendering backend, allowing the map presentation to change without changing the underlying geographic data.

---

## 🧰 Platform Setup

### Windows

```powershell
python -m pip install -e .
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_helper.ps1
osmand-preview --backend auto
```

Alternative native widget build:

```powershell
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_native_widget_msvc.ps1
```

Direct entry point:

```powershell
python src\maps\main.py --backend auto
```

### Linux

Install system dependencies first:

```bash
# Ubuntu/Debian
sudo apt-get install build-essential cmake git qt6-base-dev libqt6opengl6-dev

# Fedora/RHEL
sudo dnf install gcc g++ cmake git qt6-qtbase-devel qt6-qtbase-gui qt6-qtdeclarative-devel

# Arch
sudo pacman -S base-devel cmake git qt6-base
```

Then build and run:

```bash
python -m pip install -e .
bash tools/osmand_render_helper_native/build_linux.sh
osmand-preview --backend auto
```

Direct entry point:

```bash
python src/maps/main.py --backend auto
```

### macOS

```bash
brew install cmake qt git-lfs
git lfs install
git lfs pull
python -m pip install -e .
QT_ROOT=/opt/homebrew/opt/qt JOBS=4 bash tools/osmand_render_helper_native/build_macos.sh
osmand-preview --backend auto
```

Run either backend explicitly:

```bash
python -m maps.main --backend python
python -m maps.main --backend native
```

---

## 📚 Documentation

[![Python SDK Guide](https://img.shields.io/badge/Python_SDK_Guide-blue?style=for-the-badge)](docs/python-sdk-guide.md)
[![Python SDK Guide 中文](https://img.shields.io/badge/Python_SDK_Guide_中文-red?style=for-the-badge)](docs/python-sdk-guide.zh-CN.md)
[![Build Guide](https://img.shields.io/badge/Build_Guide-green?style=for-the-badge)](BUILD.md)
[![Notice](https://img.shields.io/badge/Notice-purple?style=for-the-badge)](NOTICE.md)

| Document | Description |
| --- | --- |
| [Python SDK Guide](docs/python-sdk-guide.md) | Python integration, runtime selection, preview usage, and embedding notes. |
| [Python SDK Guide 中文](docs/python-sdk-guide.zh-CN.md) | 中文版 Python SDK 使用指南。 |
| [Build Guide](BUILD.md) | Native helper/widget build process, platform toolchains, and runtime troubleshooting. |
| [NOTICE](NOTICE.md) | Licensing and third-party attribution context for vendored OsmAnd components. |

---

## 📝 Runtime Notes

- The preview defaults to `src/maps/tiles/World_basemap_2.obf` and the vendored resources under `vendor/osmand/resources`.
- The bundled `.obf` is a Git LFS file; run `git lfs pull` after cloning before expecting visible map content.
- The bundled `.obf` is replaceable, so you can test other OsmAnd map extracts.
- **Windows:** helper outputs in `tools/osmand_render_helper_native/dist` and `tools/osmand_render_helper_native/dist-msvc` are generated and ignored by Git.
- **Linux:** helper outputs in `tools/osmand_render_helper_native/dist-linux` are generated and ignored by Git.
- **macOS:** helper outputs in `tools/osmand_render_helper_native/dist-macosx` are generated and ignored by Git.
- When the native widget runtime is available, the preview can use the embedded OsmAnd widget; otherwise the Python rendering path remains available.
- On Linux, the native widget library is built as a `.so` file; on macOS it is built as `osmand_native_widget.dylib`.
- If you embed the native widget on Linux and hit XCB/GLX issues, see [BUILD.md](BUILD.md) and the [Python SDK Guide](docs/python-sdk-guide.md) for Qt runtime flags that must be applied before `QApplication` starts.

---

## 📄 License

Because this repository redistributes vendored OsmAnd source trees, the top-level project is documented as **GPL-3.0-or-later**.

Top-level license files:

- `LICENSE`: project-level license summary for `PySide6-OsmAnd-SDK`
- `COPYING`: verbatim GNU GPL v3 text

Vendored upstream trees keep their own original license files:

- `vendor/osmand/core/LICENSE`
- `vendor/osmand/core-legacy/LICENSE`
- `vendor/osmand/resources/LICENSE`

Additional context is summarized in [`NOTICE.md`](NOTICE.md).

---

Created by [OliverZhaohaibin](https://github.com/OliverZhaohaibin).
