#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{70FC70E5-4FC7-47F6-B046-07DF5D1FA69B}
AppName=Whisper Local Preview
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\WhisperLocalOpen
DefaultGroupName=Whisper Local Preview
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=WhisperLocal-Windows-Setup
SetupIconFile=..\build\app.ico
UninstallDisplayIcon={app}\WhisperLocal.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
#ifdef ReleaseSigning
SignTool=whisperlocal_release
SignedUninstaller=yes
SignToolRunMinimized=yes
SignToolRetryCount=2
#endif

[Files]
Source: "..\dist\WhisperLocal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked

[Icons]
Name: "{group}\Whisper Local Preview"; Filename: "{app}\WhisperLocal.exe"
Name: "{autodesktop}\Whisper Local Preview"; Filename: "{app}\WhisperLocal.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WhisperLocal.exe"; Description: "Открыть Whisper Local"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "WhisperLocalOpen"; Flags: uninsdeletevalue
