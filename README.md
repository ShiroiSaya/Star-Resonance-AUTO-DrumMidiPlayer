<p align="center">
  <img src="docs/assets/logo.png" alt="SayaTech-Midi-Studio Logo" width="160">
</p>

<h1 align="center">SayaTech-Midi-Studio</h1>

<p align="center">
  面向《星痕共鸣》的 Windows MIDI 自动演奏工具。<br>
  支持 <b>钢琴 / 吉他 / 贝斯 / 架子鼓</b>、自动调参、合奏定时、主题外观与现代化桌面界面。
</p>

<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/ShiroiSaya/SayaTech-Midi-Studio"><img alt="Repository" src="https://img.shields.io/badge/GitHub-Repository-181717?logo=github"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
</p>

<p align="center">
  <img src="docs/assets/banner.png" alt="SayaTech-Midi-Studio Banner">
</p>

## 项目简介

SayaTech-Midi-Studio 用于将 MIDI 文件转换为键盘输入，服务于《星痕共鸣》的游戏内乐器演奏场景。项目以桌面 GUI 为核心，提供 **钢琴 / 吉他 / 贝斯 / 架子鼓** 两类工作台，并围绕实际演奏需求加入了音域适配、区间移动、踏板识别、自动调参、合奏定时和可视化预览等功能。

它不是单纯的命令行脚本打包，而是一套更适合日常使用与分发的桌面工具：

- 图形化主界面与参数面板
- 钢琴 / 吉他 / 贝斯工作台
- 架子鼓工作台
- MIDI 轨道选择、卷帘预览与鼓轨预览
- 自动调参与配置模板
- 主题、夜间模式、毛玻璃与启动动画
- 适合发布的打包与安装脚本

## 预览

### 主界面

![Home](docs/assets/screenshot-home-empty.png)

### 钢琴 / 吉他 / 贝斯页面

![Piano Guitar Bass](docs/assets/screenshot-piano.png)

### 架子鼓页面

![Drum](docs/assets/screenshot-drum.png)

### 设置页面

![Settings](docs/assets/screenshot-settings.png)

### 启动画面

![Splash](docs/assets/screenshot-splash.png)

### 夜间模式

![Dark Mode](docs/assets/screenshot-dark.png)

## 功能特性

### 演奏与播放

- 钢琴 / 吉他 / 贝斯 MIDI 自动演奏
- 架子鼓 MIDI 自动演奏
- 播放 / 暂停 / 停止热键
- MIDI 轨道筛选与推荐
- 钢琴卷帘预览、鼓轨实时预览与波形辅助定位

### 音域与按键适配

- 自动适配音域
- 区间移动与短区间固定窗口逻辑
- 踏板识别与重触发控制
- 钢琴 / 吉他 / 贝斯、架子鼓两套独立参数
- 默认配置模板与可编辑 `config.txt`

### 合奏与工具能力

- 合奏定时
- 北京时间校时
- 自动调参与参数建议
- 运行日志与崩溃日志

### 界面体验

- 多主题外观
- 夜间模式
- 毛玻璃背景效果
- 可选启动动画
- 更直观的参数命名与悬停说明

## 适用环境

- Windows 10 / 11
- Python 3.10+
- PySide6 图形界面环境
- 适用于《星痕共鸣》内需要将 MIDI 映射为键盘输入的乐器演奏场景

## 安装与运行

### 从源码启动

```bash
git clone https://github.com/ShiroiSaya/SayaTech-Midi-Studio.git
cd SayaTech-Midi-Studio
pip install -r requirements.txt
python app.py
```

### Release 文件约定

仓库源码默认不包含已构建二进制文件。发布版本时可使用以下命名：

- `SayaTech_MIDI_Studio_Setup.exe`：Windows 安装包
- `SayaTech_MIDI_Studio.exe`：单文件便携版

Release 页面：<https://github.com/ShiroiSaya/SayaTech-Midi-Studio/releases>

## 构建

### 单文件 EXE

使用项目内脚本或直接通过 PyInstaller 构建：

- 输出：`dist/SayaTech_MIDI_Studio.exe`

### 安装版

推荐使用 `onedir + Inno Setup` 生成安装程序：

- 目录版输出：`dist/SayaTech_MIDI_Studio/`
- 安装包输出：`installer_output/SayaTech_MIDI_Studio_Setup.exe`

## 仓库结构

```text
.
├─ app.py
├─ sayatech_modern/
├─ docs/
│  └─ assets/
├─ scripts/
├─ SayaTech_MIDI_Studio_onefile.spec
├─ SayaTech_MIDI_Studio_onedir.spec
├─ installer.iss
├─ config.txt
├─ config.example.txt
├─ requirements.txt
└─ LICENSE
```

## 使用说明

- 浅色模式支持背景图与毛玻璃效果
- 夜间模式会自动关闭背景图，以保证界面对比度与可读性
- 程序优先读取仓库内的 `config.txt`；若缺失，会按默认模板重新生成
- README 中的界面截图来自当前项目版本界面

## License

本项目采用 MIT License，详见仓库根目录下的 [LICENSE](LICENSE) 文件。
