# Windows local preview

This launcher is prepared for 64-bit Windows with Python 3.12 and Ollama installed. Setup downloads dependencies and the local Qwen 3 4B model (approximately 2.5 GB). It uses no API subscription or GPU rental. Runtime uses your computer; speed and answer quality still need validation on it.

1. Install Python 3.12 from https://www.python.org/downloads/windows/ with the Python launcher selected, and Ollama from https://ollama.com/download/windows.
2. Extract the preview ZIP to a folder you can keep. Do not run it inside the ZIP.
3. Run `Setup.cmd` once. Keep the window open until dependency/model installation finishes.
4. For later launches run `Start.cmd`. Use the bundled launcher, rather than directly opening Milo.exe, to pass the free local configuration.
5. In Projects submit `Calculate 6 * 7`. Verify the result is 42. Then test a real research question and check its sources.

The preview contains a Milo Windows directory build and the engine source. Milo's memory remains in its existing Windows app-data directory. Engine application/workflow databases, private local key and logs live under `%LOCALAPPDATA%\AyvenEngine`, so extracting a new preview does not overwrite them. It listens only on 127.0.0.1. Existing Milo cloud/paid settings are not rewritten; this launcher passes local settings to the launched process. Quit an already-running Milo before launching so it uses these settings.

The engine remains running in the background after the launcher closes. Restarting Windows stops it; `Start.cmd` resumes stored workflows next time. Do not delete the data directory if you want to keep tasks. For custom layouts use `Start-Ayven-Milo.ps1 -MiloExecutable 'C:\path\Milo.exe'`.

Validation: Milo's 269 local tests and production renderer build pass. GitHub's Windows build validates the desktop app. Fixture task/clarification round trips and local model tool/memory integration pass. The launcher has not been executed on Windows, and this packaged preview has not been interactively tested. Real model weights could not download in the development environment due to network restrictions. This is a preview for the remaining live checks, not a completed-product claim.
