; Pass the version on the command line (build.ps1 does this from shared/version.py):
;   ISCC.exe /DAppVersion=3.5.0 installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppName=RafawaApp
AppVersion={#AppVersion}
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
; The template stays beside the app for reference; the real .env is written from the database pages below.
Source: "{#SourcePath}\.env.example"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RafawaApp"; Filename: "{app}\RafawaApp.exe"
Name: "{autodesktop}\RafawaApp"; Filename: "{app}\RafawaApp.exe"; Tasks: desktopicon

[UninstallDelete]
Type: files; Name: "{app}\.env"

[Run]
Filename: "{app}\RafawaApp.exe"; Description: "Launch RafawaApp"; Flags: nowait postinstall skipifsilent

[Code]
// Database settings are asked for during setup and written to the .env file in the install folder (nothing secret
// is stored in this installer). An existing .env is kept, so upgrades are not asked again. A silent install
// copies the template instead.
var
  DbPage, CloudPage: TInputQueryWizardPage;

function HexByte(V: Integer): String;
begin
  Result := Copy('0123456789ABCDEF', (V div 16) + 1, 1) + Copy('0123456789ABCDEF', (V mod 16) + 1, 1);
end;

// URL-encode (UTF-8) so characters such as @ $ # / in a password are safe inside the connection URL.
function UrlEncode(const S: String): String;
var
  I, C: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := Ord(S[I]);
    if ((C >= 48) and (C <= 57)) or ((C >= 65) and (C <= 90)) or ((C >= 97) and (C <= 122))
       or (C = 45) or (C = 46) or (C = 95) or (C = 126) then
      Result := Result + S[I]
    else if C < 128 then
      Result := Result + '%' + HexByte(C)
    else if C < 2048 then
      Result := Result + '%' + HexByte(192 + (C div 64)) + '%' + HexByte(128 + (C mod 64))
    else
      Result := Result + '%' + HexByte(224 + (C div 4096)) + '%' + HexByte(128 + ((C div 64) mod 64))
                       + '%' + HexByte(128 + (C mod 64));
  end;
end;

function EnvPath: String;
begin
  Result := ExpandConstant('{app}\.env');
end;

procedure InitializeWizard;
begin
  DbPage := CreateInputQueryPage(wpSelectDir, 'Database',
    'Where is the Rafawa MySQL database?',
    'Enter the connection details this PC should use. They are saved in the .env file next to the program.');
  DbPage.Add('Server address (host):', False);
  DbPage.Add('Port:', False);
  DbPage.Add('Database name:', False);
  DbPage.Add('User:', False);
  DbPage.Add('Password:', True);
  DbPage.Values[0] := '127.0.0.1';
  DbPage.Values[1] := '3306';

  CloudPage := CreateInputQueryPage(DbPage.ID, 'Cloud backup (optional)',
    'One-way backup to a cloud MySQL database',
    'Leave the host empty to turn cloud backup off. You can fill it in later by editing the .env file.');
  CloudPage.Add('Cloud host (empty = off):', False);
  CloudPage.Add('Port:', False);
  CloudPage.Add('Database name:', False);
  CloudPage.Add('User:', False);
  CloudPage.Add('Password:', True);
  CloudPage.Values[1] := '3306';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = DbPage.ID) or (PageID = CloudPage.ID) then
    Result := WizardSilent or FileExists(EnvPath);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DbPage.ID then
  begin
    if (Trim(DbPage.Values[0]) = '') or (Trim(DbPage.Values[2]) = '') or (Trim(DbPage.Values[3]) = '')
       or (DbPage.Values[4] = '') or (StrToIntDef(Trim(DbPage.Values[1]), 0) <= 0) then
    begin
      MsgBox('Please fill in the host, a numeric port, the database name, the user and the password.', mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = CloudPage.ID then
  begin
    if (Trim(CloudPage.Values[0]) <> '') and
       ((StrToIntDef(Trim(CloudPage.Values[1]), 0) <= 0) or (Trim(CloudPage.Values[2]) = '')
        or (Trim(CloudPage.Values[3]) = '') or (CloudPage.Values[4] = '')) then
    begin
      MsgBox('For cloud backup fill in the host, a numeric port, the database name, the user and the password - or empty the host to skip it.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure WriteEnvFile;
var
  T: String;
begin
  T := '# Written by the installer. Edit this file to change the database settings.' + #13#10
     + 'DB_HOST=' + Trim(DbPage.Values[0]) + #13#10
     + 'DB_PORT=' + Trim(DbPage.Values[1]) + #13#10
     + 'DB_NAME=' + Trim(DbPage.Values[2]) + #13#10
     + 'DB_USER=' + UrlEncode(Trim(DbPage.Values[3])) + #13#10
     + 'DB_PASSWORD=' + UrlEncode(DbPage.Values[4]) + #13#10
     + 'DB_CHARSET=utf8mb4' + #13#10
     + 'APP_ENV=production' + #13#10;
  if Trim(CloudPage.Values[0]) <> '' then
    T := T + 'CLOUD_DB_HOST=' + Trim(CloudPage.Values[0]) + #13#10
           + 'CLOUD_DB_PORT=' + Trim(CloudPage.Values[1]) + #13#10
           + 'CLOUD_DB_NAME=' + Trim(CloudPage.Values[2]) + #13#10
           + 'CLOUD_DB_USER=' + UrlEncode(Trim(CloudPage.Values[3])) + #13#10
           + 'CLOUD_DB_PASSWORD=' + UrlEncode(CloudPage.Values[4]) + #13#10
           + 'CLOUD_DB_SSL_CA=ca.pem' + #13#10;
  SaveStringToFile(EnvPath, T, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if (CurStep = ssPostInstall) and (not FileExists(EnvPath)) then
  begin
    if WizardSilent then
      FileCopy(ExpandConstant('{app}\.env.example'), EnvPath, False)
    else
      WriteEnvFile;
    // let ordinary users edit it later, as before
    Exec(ExpandConstant('{sys}\icacls.exe'), '"' + EnvPath + '" /grant *S-1-5-32-545:M', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
