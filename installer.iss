[Setup]
AppName=RafawaApp
AppVersion=3.4.7
DefaultDirName={autopf}\RafawaDesktop
DefaultGroupName=Rafawa Desktop
; {#SourcePath} is Inno Setup's PREPROCESSOR constant (compile-time,
; note the "#") - it always resolves to the folder containing THIS
; .iss file, on whichever PC it's compiled from. Every path in this
; script that tells the COMPILER where to find a real file (OutputDir,
; SetupIconFile, and every Source: line below in [Files]) needs this
; "#" form, since those are all resolved when the .iss is compiled -
; not the plain, no-"#" runtime {SourcePath}, which only applies to
; values resolved later, on the end user's machine after install.
OutputDir={#SourcePath}
OutputBaseFilename=RafawaAppSetup
; Windows installer/shortcut icons must be a real .ico file - a .png/
; .jpg logo needs converting first. Point this at your actual icon
; file, kept at the project root alongside this .iss file:
SetupIconFile={#SourcePath}\A-rafawa.ico
Compression=lzma
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; ADD THIS LINE BELOW TO FIX ELEVATION PERMISSIONS:
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#SourcePath}\dist\RafawaApp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\.env.template"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist; Permissions: users-modify

[Icons]
Name: "{group}\RafawaApp"; Filename: "{app}\RafawaApp.exe"
Name: "{autodesktop}\RafawaApp"; Filename: "{app}\RafawaApp.exe"; Tasks: desktopicon

[UninstallDelete]
Type: files; Name: "{app}\.env"

[Run]
Filename: "{app}\RafawaApp.exe"; Description: "Launch RafawaApp"; Flags: nowait postinstall skipifsilent