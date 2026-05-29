# CriaVideo Desktop Shell

Empacotamento Windows do editor CriaVideo em uma janela dedicada.

## Scripts

- `npm start`: abre o shell localmente.
- `npm run build:win:dir`: gera `dist/win-unpacked/` para distribuição zipada.
- `npm run build:win:portable`: gera `.exe` portátil em `dist/`.
- `npm run build:win:nsis`: gera instalador NSIS.

## Build padrão do repo

Use `powershell -ExecutionPolicy Bypass -File .\scripts\build_desktop_windows.ps1` na raiz do workspace. O script gera `win-unpacked`, compacta em `.zip` e copia para `static/downloads/`.
