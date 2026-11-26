' MBTI workbench launcher (no window)
' All paths are derived from this script's own location, so no Chinese
' literal is embedded here and renaming the folder will not break it.
Option Explicit

Dim fso, sh, base, root, py, cmd, ok
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
root = fso.GetParentFolderName(base)
py = root & "\MediaCrawler\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(py) Then
  MsgBox "Python environment not found:" & vbCrLf & py, 16, "MBTI Workbench"
  WScript.Quit 1
End If

sh.CurrentDirectory = base
cmd = """" & py & """ """ & base & "\app.py"" --open"
ok = sh.Run(cmd, 0, False)
If ok <> 0 Then
  MsgBox "Failed to start the workbench. Exit code " & ok, 16, "MBTI Workbench"
End If
