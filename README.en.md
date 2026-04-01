<p align="center">
  <img src="docs/assets/logo.png" alt="SayaTech-Midi-Studio Logo" width="160">
</p>

<h1 align="center">SayaTech-Midi-Studio</h1>

<p align="center">
  A Windows MIDI auto-performance tool for <b>Star Resonance</b>.<br>
  Supports <b>Piano / Guitar / Bass / Drum</b>, auto tuning, ensemble timing, themes, and a modern desktop UI.
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

## Overview

SayaTech-Midi-Studio converts MIDI files into keyboard input for in-game instrument performance in **Star Resonance**. The project is built around a desktop GUI and provides workspaces for **Piano / Guitar / Bass** and **Drum**, together with range adaptation, interval movement, sustain pedal handling, auto tuning, ensemble timing, and visual previews.

It is not just a packaged script. It is a desktop workflow designed for daily use and distribution:

- GUI main window and parameter panels
- Piano / Guitar / Bass workspace
- Drum workspace
- MIDI track selection, piano-roll preview, and drum preview
- Auto tuning and config templates
- Themes, dark mode, glass effects, and splash screen
- Packaging and installer scripts for release builds

## Preview

### Main Window

![Home](docs/assets/screenshot-home-empty.png)

### Piano / Guitar / Bass Page

![Piano Guitar Bass](docs/assets/screenshot-piano.png)

### Drum Page

![Drum](docs/assets/screenshot-drum.png)

### Settings Page

![Settings](docs/assets/screenshot-settings.png)

### Splash Screen

![Splash](docs/assets/screenshot-splash.png)

### Dark Mode

![Dark Mode](docs/assets/screenshot-dark.png)

## Features

### Performance and Playback

- Piano / Guitar / Bass MIDI auto-play
- Drum MIDI auto-play
- Play / Pause / Stop hotkeys
- MIDI track filtering and recommendations
- Piano-roll preview, drum preview, and timeline-assisted seeking

### Range and Key Mapping

- Automatic playable-range adaptation
- Interval movement and short-range fixed-window logic
- Sustain pedal handling and retrigger control
- Separate parameter sets for Piano / Guitar / Bass and Drum
- Default templates with editable `config.txt`

### Ensemble and Tools

- Ensemble scheduling
- Beijing time sync
- Auto tuning and parameter suggestions
- Runtime logs and crash logs

### UI Experience

- Multiple themes
- Dark mode
- Glass background effect
- Optional splash screen
- Clearer parameter naming and hover hints

## Environment

- Windows 10 / 11
- Python 3.10+
- PySide6 desktop GUI environment
- Intended for instrument performance in **Star Resonance**, where MIDI is mapped to keyboard input

## Installation and Run

### Run from Source

```bash
git clone https://github.com/ShiroiSaya/SayaTech-Midi-Studio.git
cd SayaTech-Midi-Studio
pip install -r requirements.txt
python app.py
```

### Release Naming

Source repositories do not include built binaries by default. Recommended release filenames:

- `SayaTech_MIDI_Studio_Setup.exe`: Windows installer
- `SayaTech_MIDI_Studio.exe`: single-file portable build

Releases: <https://github.com/ShiroiSaya/SayaTech-Midi-Studio/releases>

## Build

### Single-file EXE

Build with the included scripts or directly with PyInstaller:

- Output: `dist/SayaTech_MIDI_Studio.exe`

### Installer Build

Using `onedir + Inno Setup` is recommended for faster startup and more stable distribution:

- Directory build: `dist/SayaTech_MIDI_Studio/`
- Installer output: `installer_output/SayaTech_MIDI_Studio_Setup.exe`

## Repository Structure

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

## Notes

- Light mode supports a background image and glass effects
- Dark mode disables the background automatically for readability and stability
- The app reads `config.txt` first; if it is missing, a default template is generated automatically
- The screenshots in this README are taken from the current project UI

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.
