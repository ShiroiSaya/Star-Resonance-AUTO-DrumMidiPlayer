#define MyAppVersion "1.0.6"
#define MyAppName "SayaTech MIDI Studio"
#define MyAppPublisher "SayaTech"
#define MyAppExeName "SayaTech_MIDI_Studio.exe"
#define MyAppDisplayName "SayaTech MIDI Studio 满血版"
#define MyOutputBaseFilename "SayaTech_MIDI_Studio_GPU_Setup_v1.0.6"
#define MyAppId "{{5F172237-6D1B-4C7E-A3B8-B58BF199B8C9}"
#define MyDistDir "dist\\SayaTech_MIDI_Studio_GPU"

[Setup]
AppId={#MyAppId}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppDisplayName}
DefaultGroupName={#MyAppDisplayName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile=sayatech_modern\assets\app.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion=1.0.6.0

[Languages]
Name: "chinesesimplified"; MessagesFile: ".\\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppDisplayName}"; Flags: nowait postinstall skipifsilent
