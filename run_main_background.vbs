Set WshShell = CreateObject("WScript.Shell")
' Intenta ejecutar pythonw.exe desde el entorno virtual.
' Se asume que este script VBS se ejecuta desde el directorio raíz del proyecto.
WshShell.Run "%COMSPEC% /c .venv\Scripts\pythonw.exe main.py", 0, False
Set WshShell = Nothing 