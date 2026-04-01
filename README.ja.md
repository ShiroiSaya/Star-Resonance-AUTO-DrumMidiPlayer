<p align="center">
  <img src="docs/assets/logo.png" alt="SayaTech-Midi-Studio Logo" width="160">
</p>

<h1 align="center">SayaTech-Midi-Studio</h1>

<p align="center">
  <b>星痕共鳴</b>向けの Windows 用 MIDI 自動演奏ツール。<br>
  <b>ピアノ / ギター / ベース / ドラム</b>、自動調整、合奏タイマー、テーマ切替、モダンなデスクトップ UI に対応。
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

## 概要

SayaTech-Midi-Studio は、**星痕共鳴** のゲーム内楽器演奏向けに、MIDI ファイルをキーボード入力へ変換するデスクトップツールです。GUI を中心に構成されており、**ピアノ / ギター / ベース** と **ドラム** のワークスペース、音域適応、区間移動、サステイン処理、自動調整、合奏タイマー、可視化プレビューを備えています。

単なるスクリプト集ではなく、日常利用と配布に適したデスクトップワークフローとして設計されています。

- GUI メイン画面とパラメータパネル
- ピアノ / ギター / ベース用ワークスペース
- ドラム用ワークスペース
- MIDI トラック選択、ピアノロール表示、ドラムプレビュー
- 自動調整と設定テンプレート
- テーマ、ダークモード、ガラス風背景、スプラッシュ画面
- リリース向けのパッケージ化とインストーラスクリプト

## プレビュー

### メイン画面

![Home](docs/assets/screenshot-home-empty.png)

### ピアノ / ギター / ベース画面

![Piano Guitar Bass](docs/assets/screenshot-piano.png)

### ドラム画面

![Drum](docs/assets/screenshot-drum.png)

### 設定画面

![Settings](docs/assets/screenshot-settings.png)

### スプラッシュ画面

![Splash](docs/assets/screenshot-splash.png)

### ダークモード

![Dark Mode](docs/assets/screenshot-dark.png)

## 主な機能

### 演奏と再生

- ピアノ / ギター / ベース MIDI 自動演奏
- ドラム MIDI 自動演奏
- 再生 / 一時停止 / 停止ホットキー
- MIDI トラックの選別と推奨
- ピアノロール、ドラムプレビュー、タイムラインによる位置確認

### 音域とキー適応

- 演奏可能音域の自動適応
- 区間移動と短区間固定ウィンドウのロジック
- サステイン処理と再トリガー制御
- ピアノ / ギター / ベース用とドラム用の独立パラメータ
- 編集可能な `config.txt` と既定テンプレート

### 合奏と補助機能

- 合奏タイマー
- 北京時間の同期
- 自動調整とパラメータ提案
- 実行ログとクラッシュログ

### UI / 体験

- 複数テーマ
- ダークモード
- ガラス風背景効果
- 任意で使えるスプラッシュ画面
- より分かりやすいパラメータ名とホバー説明

## 動作環境

- Windows 10 / 11
- Python 3.10+
- PySide6 デスクトップ GUI 環境
- **星痕共鳴** で MIDI をキーボード入力へ変換して演奏する用途向け

## インストールと実行

### ソースから実行

```bash
git clone https://github.com/ShiroiSaya/SayaTech-Midi-Studio.git
cd SayaTech-Midi-Studio
pip install -r requirements.txt
python app.py
```

### Release の命名

ソースリポジトリには通常ビルド済みバイナリは含みません。推奨される配布ファイル名は次の通りです。

- `SayaTech_MIDI_Studio_Setup.exe`：Windows インストーラ
- `SayaTech_MIDI_Studio.exe`：単一ファイル版

Releases: <https://github.com/ShiroiSaya/SayaTech-Midi-Studio/releases>

## ビルド

### 単一ファイル EXE

付属スクリプト、または PyInstaller を使ってビルドできます。

- 出力先：`dist/SayaTech_MIDI_Studio.exe`

### インストーラ版

起動速度と配布の安定性を考えると、`onedir + Inno Setup` の構成を推奨します。

- ディレクトリ版：`dist/SayaTech_MIDI_Studio/`
- インストーラ出力：`installer_output/SayaTech_MIDI_Studio_Setup.exe`

## リポジトリ構成

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

## 補足

- ライトモードでは背景画像とガラス風効果を利用できます
- ダークモードでは可読性と安定性のため背景画像を自動で無効化します
- アプリはまず `config.txt` を読み込み、存在しない場合は既定テンプレートを自動生成します
- README に掲載している画面キャプチャは現行バージョンの UI です

## License

このプロジェクトは MIT License の下で公開されています。詳細は [LICENSE](LICENSE) を参照してください。
