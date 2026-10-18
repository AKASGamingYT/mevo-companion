#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "Mevo Companion"
#define MyAppPublisher "Mevo Companion"
#define MyAppURL "https://github.com/AKASGamingYT/mevo-companion"
#define MyAppExeName "MevoCompanion.exe"

[Setup]
AppId={{8F3C1A2E-6B47-4D9A-9C21-7E5B0A4D6F18}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\Mevo Companion
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=MevoCompanion-Setup-x64
SetupIconFile=..\src\mevo_companion\assets\mevo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
CloseApplications=no

[Tasks]
Name: desktopicon; Description: "Create a desktop icon"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: streamdeck; Description: "Install the Stream Deck plugin"; GroupDescription: "Extras:"; Flags: checkedonce

[Files]
Source: "..\dist\MevoCompanion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\streamdeck\com.mevo-companion.sdPlugin\*"; DestDir: "{userappdata}\Elgato\StreamDeck\Plugins\com.mevo-companion.sdPlugin"; Flags: ignoreversion recursesubdirs createallsubdirs; Tasks: streamdeck

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
