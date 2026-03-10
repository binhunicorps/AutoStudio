Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Function Q(s)
    Q = Chr(34) & s & Chr(34)
End Function

Function WaitForServer(url, timeoutSeconds)
    Dim startAt, ok, http
    startAt = Now
    ok = False
    Do While DateDiff("s", startAt, Now) < timeoutSeconds
        On Error Resume Next
        Set http = CreateObject("MSXML2.XMLHTTP")
        http.Open "GET", url, False
        http.Send
        If Err.Number = 0 Then
            If http.Status >= 200 And http.Status < 500 Then
                ok = True
            End If
        End If
        On Error GoTo 0
        If ok Then Exit Do
        WScript.Sleep 300
    Loop
    WaitForServer = ok
End Function

Sub StopServerByPort5000()
    On Error Resume Next
    WshShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do taskkill /F /PID %a >nul 2>&1", 0, True
    On Error GoTo 0
End Sub

Function FindChrome()
    ' Registry paths
    Dim regKeys, regKey
    regKeys = Array( _
        "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe\", _
        "HKLM\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe\", _
        "HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe\" _
    )
    For Each regKey In regKeys
        On Error Resume Next
        Dim regVal
        regVal = WshShell.RegRead(regKey)
        On Error GoTo 0
        If Len(regVal & "") > 0 Then
            If fso.FileExists(regVal) Then
                FindChrome = regVal
                Exit Function
            End If
        End If
    Next

    ' Common file paths
    Dim paths, p
    paths = Array( _
        WshShell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe", _
        WshShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe", _
        WshShell.ExpandEnvironmentStrings("%LocalAppData%") & "\Google\Chrome\Application\chrome.exe" _
    )
    For Each p In paths
        If fso.FileExists(p) Then
            FindChrome = p
            Exit Function
        End If
    Next

    FindChrome = ""
End Function

' ── Main ─────────────────────────────────────────────────────────────────────
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
runServerScript = appDir & "\scripts\run_server.bat"
serverLog = appDir & "\runtime\server_boot.log"

If Not fso.FileExists(runServerScript) Then
    MsgBox "Missing script: scripts\run_server.bat", vbCritical, "Auto Studio"
    WScript.Quit 1
End If

' Find Chrome
chromeExe = FindChrome()
If Len(chromeExe) = 0 Then
    MsgBox "Google Chrome was not found on this machine.", vbCritical, "Auto Studio"
    WScript.Quit 1
End If

StopServerByPort5000

' Start server hidden
WshShell.Run "cmd /c cd /d " & Q(appDir) & " && call scripts\run_server.bat > " & Q(serverLog) & " 2>&1", 0, False

' Wait for server
If Not WaitForServer("http://localhost:5000/api/config", 300) Then
    StopServerByPort5000
    If fso.FileExists(serverLog) Then
        WshShell.Run "notepad " & Q(serverLog), 1, False
    End If
    MsgBox "Server khong the khoi dong." & vbCrLf & _
           "Da mo file log: runtime\server_boot.log", _
           vbCritical, "Auto Studio"
    WScript.Quit 1
End If

' Create temporary user data dir for guest session
runtimeDir = appDir & "\runtime"
If Not fso.FolderExists(runtimeDir) Then fso.CreateFolder(runtimeDir)
guestBase = runtimeDir & "\chrome-guest-session"
If Not fso.FolderExists(guestBase) Then fso.CreateFolder(guestBase)

sessionName = "session_" & Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2) _
              & "_" & Right("0" & Hour(Now), 2) & Right("0" & Minute(Now), 2) & Right("0" & Second(Now), 2)
guestSessionDir = guestBase & "\" & sessionName
fso.CreateFolder(guestSessionDir)

' Launch Chrome in Guest/App mode directly (no PowerShell!)
chromeArgs = "--guest --new-window --app=http://localhost:5000" _
           & " --window-size=1400,900 --disable-infobars --no-first-run" _
           & " --no-default-browser-check" _
           & " --user-data-dir=" & Q(guestSessionDir)

Set chromeProc = WshShell.Exec(Q(chromeExe) & " " & chromeArgs)

' Wait for Chrome to close
Do While chromeProc.Status = 0
    WScript.Sleep 1000
Loop

' Cleanup guest session and stop server
On Error Resume Next
fso.DeleteFolder guestSessionDir, True
On Error GoTo 0
StopServerByPort5000

WScript.Quit 0
