# AGENTS.md

Este arquivo serve como instrução operacional para qualquer IA/agente que abrir este workspace.

Se houver conflito entre este arquivo e um pedido direto do usuário, o pedido direto do usuário vence.

## Projeto

- Nome: CriaVideo / criavideo.pro
- Workspace local: `C:\Users\User\Desktop\Criar Vídeos`
- Deploy remoto: `root@criavideo.pro:/opt/levita-video`
- Container principal no VPS: `levita-video`

## Regras obrigatórias deste workspace

1. O fluxo padrao deste workspace agora e staging-first, no mesmo estilo de Levita e Tevoxi.
2. Sempre concluir alteracoes normais com esta ordem:
   - validar localmente
   - rodar `git add -A && git commit -m "..."`
   - publicar no staging com `git push staging master`
   - validar staging
   - rodar `git push`
3. Nunca encerrar uma tarefa com alteracoes funcionais sem staging + push.
4. Nao publicar em producao sem pedido explicito do usuario.
5. Nao usar Playwright para validar deploy. O usuário testa manualmente no navegador.
6. Não reverter mudanças não relacionadas que já existirem no workspace.
7. Não usar `git reset --hard`, `git checkout --`, `git push --force` ou comandos destrutivos sem pedido explícito.

## Acesso / credenciais

- O deploy deste projeto normalmente usa acesso SSH já configurado nesta máquina para `root@criavideo.pro`.
- Não existe senha de VPS armazenada neste repositório.
- Se o SSH pedir uma senha desconhecida ou a chave falhar, parar e pedir orientação ao usuário.
- Não inventar credenciais.

## Fluxo padrão de deploy

- Deploy normal do dia a dia: `git push staging master`
- Validacao normal depois do deploy: `powershell -ExecutionPolicy Bypass -File .\scripts\verify_frontend_bundle_sync.ps1 -CompareStaging`
- Producao so entra quando o usuario pedir explicitamente para promover o commit validado em staging.

## Staging / sandbox

- Staging oficial planejado: `https://staging.criavideo.pro`.
- O fluxo de staging deve espelhar o workspace Levita:
   - repo bare no VPS: `/opt/levita-video-staging-repo.git`
   - working tree no VPS: `/opt/levita-video-staging`
   - remote local: `staging`
   - deploy: `git push staging master`
   - compose: `docker compose -f docker-compose.staging.yml -p levita-video-staging up -d --build`
- O hook de staging nunca deve rodar `git clean`, `docker compose down`, prune de Docker ou remocao de volumes.
- Antes de qualquer mudanca chegar em producao, publicar primeiro no staging e o usuario validar manualmente no navegador.
- Producao continua protegida: nao publicar em `criavideo.pro` sem pedido explicito do usuario.
- Validacao de bundle no staging: `powershell -ExecutionPolicy Bypass -File .\scripts\verify_frontend_bundle_sync.ps1 -CompareStaging`.

Nao usar `./deploy.sh` como fluxo padrão no Windows para publicar mudanças normais. O deploy padrao agora e staging via git remote. `./deploy.sh` fica restrito a casos em que o usuario pedir producao explicitamente.

Usar este fluxo por padrão:

1. Validar localmente
2. Rodar `git add -A && git commit -m "..."`
3. Rodar `git push staging master`
4. Validar staging
5. Rodar `git push`

## Fluxo de producao

- Producao so deve ser publicada quando o usuario pedir explicitamente.
- Ao promover para producao, usar o mesmo commit que ja foi validado em staging.
- Para producao, continuar usando o fluxo seguro de `scp` + `docker compose up -d --build` + validacao remota.

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
- Em qualquer UI nova, priorizar visual moderno, pouco texto, mais ícones e menos blocos explicativos.
- Evitar visual genérico/clean demais quando o pedido permitir algo mais marcante e direto.
- Se a tarefa tocar qualquer arquivo do bundle frontend (`static/app.js`, `static/index.html`, `static/style.css`, `static/pwa.js`, `static/sw.js`), tratar esses arquivos como um conjunto. Nunca restaurar ou publicar só HTML/JS sem conferir o CSS correspondente e o versionamento do PWA.
- Antes de editar hotfix de frontend, rodar `git status --short`. Se algum arquivo do bundle frontend já estiver modificado por outra janela, NAO editar por cima no workspace principal. Criar um `git worktree` limpo no commit atual e fazer o hotfix nele para evitar reverter partes recentes sem perceber.
- Em qualquer restauracao de recurso visual, validar localmente pelo menos 1 token de cada camada antes do deploy:
   - HTML: id/classe/texto do controle restaurado
   - JS: funcao que controla o recurso
   - CSS: seletor principal do visual restaurado
   - PWA: versao/query string sincronizada
- Antes de qualquer deploy/frontend commit que toque `static/app.js`, `static/index.html`, `static/style.css`, `static/pwa.js` ou `static/sw.js`, rodar `powershell -ExecutionPolicy Bypass -File .\scripts\verify_frontend_bundle_sync.ps1` para validar sincronismo local do bundle.
- Na validacao remota de frontend, nao conferir so versao. Sempre fazer `grep` no VPS para pelo menos 1 token de HTML, 1 de JS e 1 de CSS do recurso alterado. Se qualquer camada estiver ausente, nao commitar.
- Se o hotfix for de modal/componente interativo com `onclick`, `onchange` ou botoes segmentados, confirmar tambem que as funcoes referenciadas ainda existem no `static/app.js` publicado.
- Depois de qualquer hotfix no editor, validar no mínimo:
   - se houve deploy real no staging
  - se o token novo de versão entrou no servidor
   - se HTML + JS + CSS do recurso alterado chegaram juntos no servidor
   - rodar `powershell -ExecutionPolicy Bypass -File .\scripts\verify_frontend_bundle_sync.ps1 -CompareStaging` para confirmar local = host do VPS = container ativo = HTML publico do staging
   - se o usuario pedir promocao, rodar tambem `powershell -ExecutionPolicy Bypass -File .\scripts\verify_frontend_bundle_sync.ps1 -CompareProduction`
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
2. Rodar `git add -A && git commit -m "..."`
3. Fazer deploy no staging com `git push staging master`
4. Validar staging
5. Rodar `git push`
6. Confirmar que `git status --short` está limpo

## O que não fazer

- Não assumir que build OK significa deploy OK
- Não depender só de cache local do navegador para validar frontend
- Não usar `./deploy.sh` como padrão no Windows quando o objetivo é publicar hotfix específico
- Não incluir credenciais inventadas ou não verificadas neste repositório
