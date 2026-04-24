# AGENTS.md

Este arquivo serve como instrução operacional para qualquer IA/agente que abrir este workspace.

Se houver conflito entre este arquivo e um pedido direto do usuário, o pedido direto do usuário vence.

## Projeto

- Nome: CriaVideo / criavideo.pro
- Workspace local: `C:\Users\User\Desktop\Criar Vídeos`
- Deploy remoto: `root@criavideo.pro:/opt/levita-video`
- Container principal no VPS: `levita-video`

## Regras obrigatórias deste workspace

1. Sempre concluir alterações com esta ordem:
   - primeiro fazer deploy no VPS
   - depois rodar `git add -A && git commit -m "..." && git push`
2. Nunca encerrar uma tarefa com alterações funcionais sem deploy + push.
3. Não usar Playwright para validar deploy. O usuário testa manualmente no navegador.
4. Não reverter mudanças não relacionadas que já existirem no workspace.
5. Não usar `git reset --hard`, `git checkout --`, `git push --force` ou comandos destrutivos sem pedido explícito.

## Acesso / credenciais

- O deploy deste projeto normalmente usa acesso SSH já configurado nesta máquina para `root@criavideo.pro`.
- Não existe senha de VPS armazenada neste repositório.
- Se o SSH pedir uma senha desconhecida ou a chave falhar, parar e pedir orientação ao usuário.
- Não inventar credenciais.

## Fluxo padrão de deploy

Nao usar `./deploy.sh` como fluxo padrão no Windows para publicar pequenas mudanças. Ele pode deixar código antigo no servidor.

Usar este fluxo por padrão:

1. Copiar somente os arquivos alterados com `scp`
2. No VPS, rebuildar com `docker compose up -d --build`
3. Validar os tokens/linhas alteradas no servidor com `grep`
4. Só depois commitar e dar push

### Exemplo de deploy de frontend

```powershell
scp static/app.js root@criavideo.pro:/opt/levita-video/static/app.js
scp static/index.html root@criavideo.pro:/opt/levita-video/static/index.html
scp static/pwa.js root@criavideo.pro:/opt/levita-video/static/pwa.js
scp static/sw.js root@criavideo.pro:/opt/levita-video/static/sw.js
ssh root@criavideo.pro "cd /opt/levita-video && docker compose up -d --build"
```

### Exemplo de deploy de backend

```powershell
scp app/routers/video.py root@criavideo.pro:/opt/levita-video/app/routers/video.py
ssh root@criavideo.pro "cd /opt/levita-video && docker compose up -d --build"
```

### Exemplo de validacao remota

```powershell
ssh root@criavideo.pro "grep -n 'TOKEN_OU_TRECHO_ALTERADO' /opt/levita-video/static/app.js"
```

Regra crítica:

- `docker compose restart` nao substitui build de imagem
- usar sempre `docker compose up -d --build`

## Regras de versionamento frontend / PWA

Em qualquer deploy que altere frontend, atualizar sempre estes 4 pontos juntos:

1. `static/app.js`
   - atualizar o marcador visível no console: `"[CriaVideo] app.js vNNN loaded"`
2. `static/index.html`
   - subir `REQUIRED_VER`
   - atualizar query strings de `style.css`, `app.js` e `pwa.js`
3. `static/pwa.js`
   - atualizar a query string do `sw.js`
4. `static/sw.js`
   - subir `CACHE_NAME`
   - atualizar as query strings dos assets cacheados

Se esses 4 pontos não forem sincronizados, o navegador pode continuar carregando JS antigo por cache do service worker.

## Regras específicas de edição do editor

- Ao mexer no editor (`static/app.js`, `static/index.html`, `static/style.css`), preservar recursos já restaurados anteriormente.
- Depois de qualquer hotfix no editor, validar no mínimo:
  - se houve deploy real no VPS
  - se o token novo de versão entrou no servidor
  - se o `git status` ficou limpo após commit/push

## Problemas operacionais conhecidos

### Build do Docker sem espaço

Se o build falhar com `no space left on device`:

1. Garantir que `.dockerignore` continue excluindo artefatos pesados:
   - `app/media/`
   - `android-build/`
   - `debug_frames/`
   - `seedance/videos/`
   - `temp_output.txt`
2. Limpar somente artefatos Docker não usados:

```powershell
ssh root@criavideo.pro "docker image prune -af"
ssh root@criavideo.pro "docker builder prune -af"
```

3. Rodar novamente:

```powershell
ssh root@criavideo.pro "cd /opt/levita-video && docker compose up -d --build"
```

### Migração SQL falhando por tabela ausente

Se aparecer erro como `relation ... does not exist` durante migração SQL:

```powershell
ssh root@criavideo.pro "docker exec -e PYTHONPATH=/app levita-video python /app/migrate.py"
```

Depois disso, aplicar a migração SQL novamente.

## Checklist obrigatório ao terminar qualquer tarefa

1. Validar arquivo(s) alterados localmente
2. Fazer deploy no VPS
3. Validar conteúdo real no servidor com `grep`
4. Rodar `git add -A && git commit -m "..." && git push`
5. Confirmar que `git status --short` está limpo

## O que não fazer

- Não assumir que build OK significa deploy OK
- Não depender só de cache local do navegador para validar frontend
- Não usar `./deploy.sh` como padrão no Windows quando o objetivo é publicar hotfix específico
- Não incluir credenciais inventadas ou não verificadas neste repositório
