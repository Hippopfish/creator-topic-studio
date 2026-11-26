' MBTI workbench stopper (no window)
Option Explicit

Dim fso, sh, base, root, py, cmd
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
cmd = """" & py & """ """ & base & "\bin\stop.py"""
sh.Run cmd, 0, True
