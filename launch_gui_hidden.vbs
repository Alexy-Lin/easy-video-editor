Option Explicit

Dim shell, fileSystem, scriptDir, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
command = Chr(34) & scriptDir & "\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & scriptDir & "\video_cut.py" & Chr(34)
shell.Run command, 0, False
