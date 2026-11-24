#define AppVersion "2026.11.24"
[Setup]
AppId={{5D0C8B7E-E608-42A1-9150-84877F3794DB}
AppName=TK 上架助手
AppVersion={#AppVersion}
AppPublisher=TK 上架助手
DefaultDirName={localappdata}\Programs\TKShangjia
DefaultGroupName=TK 上架助手
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\..\release
OutputBaseFilename=TKShangjia-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\TKShangjia.exe
CloseApplications=yes
RestartApplications=no
[Messages]
SetupWindowTitle=TK 上架助手安装
WelcomeLabel1=欢迎安装 TK 上架助手
WelcomeLabel2=本软件在当前电脑独立运行。每位使用者需要自己的 TikTok 应用和店铺授权。
SelectDirLabel3=请选择程序安装位置。商品数据和配置会保存在当前 Windows 用户的数据目录中。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonCancel=取消
ButtonInstall=安装(&I)
ButtonFinish=完成(&F)
FinishedHeadingLabel=TK 上架助手安装完成
FinishedLabel=点击完成后打开软件。Python、采集浏览器和中英文文字识别组件已随程序安装。首次使用请进入「店铺管理」连接店铺。
[Files]
Source: "..\dist\TKShangjia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "employee-readme.txt"; DestDir: "{app}"; DestName: "安装使用说明.txt"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\TK 上架助手"; Filename: "{app}\TKShangjia.exe"
Name: "{autodesktop}\TK 上架助手"; Filename: "{app}\TKShangjia.exe"
[Run]
Filename: "{app}\TKShangjia.exe"; Description: "打开 TK 上架助手"; Flags: nowait postinstall skipifsilent
; User data is outside {app} and intentionally retained by the uninstaller.
