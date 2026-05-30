# CriaVideo Desktop Shell

Empacotamento Windows do editor CriaVideo em uma janela dedicada.

## Scripts

- `npm start`: abre o shell localmente.
- `npm run build:win:dir`: gera `dist/win-unpacked/` para distribuição zipada.
- `npm run build:win:portable`: gera `.exe` portátil em `dist/`.
- `npm run build:win:nsis`: gera instalador NSIS.

## Build padrão do repo

Use `powershell -ExecutionPolicy Bypass -File .\scripts\build_desktop_windows.ps1` na raiz do workspace. O script gera `win-unpacked`, compacta em `.zip` e copia para `static/downloads/`.

## Runtime local (primeiro slice)

- O shell agora aceita um modo opcional `local-proxy` em `desktop-config.json`.
- Para desenvolvimento local no Windows, use `npm run start:local-proxy` dentro de `desktop/windows-shell`.
- Nesse modo, o Electron sobe `desktop/local-runtime/app.py`, espera `GET /video/health` em `http://127.0.0.1:3232/video/health` e abre o editor em `http://127.0.0.1:3232/video`.
- O runtime local deste primeiro slice ainda serve o frontend local e faz proxy de `/api/*` e `/video/media/*` para o host configurado em `runtime.apiTargetUrl`.
- O valor padrão continua `remote` para não quebrar o pacote atual enquanto o runtime local definitivo ainda está em construção.
