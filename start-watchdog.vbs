' Запускает watchdog.bat скрыто (без окна). Кладётся в автозагрузку Windows,
' чтобы сторож следил за ботом с момента входа в систему.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c """ & dir & "\watchdog.bat""", 0, False
