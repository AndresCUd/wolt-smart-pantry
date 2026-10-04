Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = strPath & "\.venv\Scripts\pythonw.exe"
scriptFile = strPath & "\telegram_bot.py"
WshShell.CurrentDirectory = strPath
WshShell.Run """" & pythonExe & """ """ & scriptFile & """", 0, False
