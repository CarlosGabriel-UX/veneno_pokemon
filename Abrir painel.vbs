' Abre o painel sem o terminal preto (dois cliques neste arquivo).
' pythonw não cria terminal; o 1 no Run é para a janela do painel aparecer normalmente.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
pasta = fso.GetParentFolderName(WScript.ScriptFullName) & "\game-bot-dashboard"
shell.CurrentDirectory = pasta
On Error Resume Next
shell.Run "pythonw """ & pasta & "\src\main.py""", 1, False
If Err.Number <> 0 Then
  Err.Clear
  shell.Run "pyw """ & pasta & "\src\main.py""", 1, False
End If
If Err.Number <> 0 Then
  MsgBox "Nao achei o Python. Instale o Python marcando ""Add python.exe to PATH"".", 16, "Veneno do Pokemon"
End If
