' JARVIS_Start.vbs — Silent launcher for the JARVIS system tray app.
' Double-click this file (or place it in Windows Startup) to start JARVIS
' without a console window appearing.
'
' Requires: pip install Pillow pystray playwright websockets

Set WshShell = CreateObject("WScript.Shell")

' Detect pythonw (no console) next to the regular python
Dim pythonDir
pythonDir = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python"

' Get the directory of this .vbs file
Dim scriptDir
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

Dim cmd
cmd = "pythonw """ & scriptDir & "\backend\local\tray_app.py"""

' Run hidden (0 = hidden window, False = don't wait)
WshShell.Run cmd, 0, False
