# Painelzinho da Keile

Aplicação independente para conferência de DDA. Python 3.12, SQLite persistente e eventos SSE. Nenhum dado financeiro real, foto pessoal ou credencial de produção é armazenado neste repositório público.

## Teste local

Execute `python demo.py` com Python 3.10+. Informe os dois e-mails de homologação quando solicitado. A senha de ambos é 123456. Abra http://127.0.0.1:8080. Clique em **Simular DDA** para receber três cobranças fictícias individualmente. O modo demo usa banco temporário, não aceita upload real ou webhook externo e não acessa Omie ou SharePoint.

## Vercel (produção recomendada para esta implantação)

Importe a branch `main` deste repositório em um projeto Vercel, framework Flask, diretório raiz. O entrypoint é `app.py`; o servidor Docker antigo continua disponível para testes locais. Use uma equipe/plano apropriado para uso empresarial e aprovado pela empresa.

Configure um PostgreSQL **dedicado ao painel** (por exemplo Neon via Marketplace), com TLS, credenciais restritas e conexão poolada. Não aponte para um banco compartilhado com tabelas de mesmo nome. As tabelas são inicializadas automaticamente. Defina estas variáveis privadas apenas no ambiente de produção:

- `DATABASE_URL` (ou `POSTGRES_URL`): conexão PostgreSQL com TLS.
- `PUBLIC_ORIGIN`: domínio HTTPS exato do painel, sem barra final.
- `PANEL_USERS_JSON`: JSON com contas individuais e senhas únicas de pelo menos 16 caracteres.
- `WEBHOOK_TOKEN`: token aleatório dedicado de pelo menos 32 caracteres para eventos n8n → painel.
- `N8N_UPLOAD_URL`: `https://n8n.guinevesapi.xyz/webhook/painelzinho-keile-entrada`.
- `N8N_UPLOAD_HEADER`: `apikey` (valor padrão).
- `N8N_UPLOAD_TOKEN`: valor da credencial Header Auth do webhook de entrada, pelo menos 32 caracteres.
- `PANEL_OPERATIONS`: `PAJ,PAC` (ampliar somente após homologar operações).

Não configure DEMO_MODE em produção. Nenhum segredo deve entrar no GitHub. Em n8n, configurar PAINELZINHO_EVENTS_URL como `https://DOMINIO_DO_PAINEL/api/events` e associar credencial Authorization: Bearer ao publicador. Se houver proteção de deployment, permitir o webhook externo de forma controlada sem remover a autenticação da aplicação.

SSE reconecta periodicamente (24 s), lendo eventos persistidos no PostgreSQL. Upload aceita XLSX até 4 MiB; aguarda ACK do n8n em vez de iniciar thread em background. Em falha, reenvie o mesmo arquivo; identificador SHA-256 preserva idempotência. Arquivos maiores exigem upload direto com storage privado ou transferência em partes. Não habilitar produção antes de testar o pipeline fiscal emitindo eventos por linha, validação de credencial, reenvio, conferência compartilhada e duas sessões simultâneas.

Validação: `/health` deve retornar 200 e `{"ok":true}`; sem autenticação, `/api/snapshot` deve retornar 401. Testar primeiro com dados fictícios em ambiente separado. A presença do código no GitHub não comprova deploy, configuração de banco ou integração fiscal em produção.

## Produção em servidor tradicional (alternativa)

Hospede o Dockerfile da raiz em serviço privado, porta 8080, volume persistente /data gravável pelo UID 10001, proxy HTTPS e buffering SSE desabilitado. Configure variáveis de .env.example com credenciais únicas fortes (>=16 caracteres), URL HTTPS, operações permitidas, token webhook dedicado (>=32 caracteres), URL e token do n8n. Nunca habilite DEMO_MODE na produção. Configure backups, rate limit e acesso corporativo. Para múltiplas réplicas migre para PostgreSQL.

## Contrato de integração

POST /api/events com Authorization: Bearer <WEBHOOK_TOKEN>. Evento JSON com type, batch_id, event_id estável e revision inteira crescente. Tipos: batch.started (name,total), row.upsert (row_id,row), batch.completed (total), batch.failed (error). row inclui operacao, fornecedor, documento, cnpj, valor_centavos inteiro, vencimento, status, nota, detalhe, links[{label,url}]. Enviar cada resultado na conclusão de sua unidade de trabalho, antes de consolidar. Não inventar correspondências fiscais. IDs de lote e linha devem permanecer estáveis durante reenvio. Conferência humana é compartilhada e reaberta quando evidência muda.

Exemplo: {"type":"row.upsert","batch_id":"lote1","event_id":"lote1:linha1:2","revision":2,"row_id":"PAJ:12","row":{"operacao":"PAJ","fornecedor":"Fornecedor fictício","valor_centavos":28719,"status":"REVISAR"}}

Upload do painel envia JSON batch_id, filename, xlsx_base64 ao webhook configurado e exige ACK accepted:true e batch_id correspondente. Integração real requer configuração e homologação do n8n. Sem publicação automática no GitHub Pages: exige backend.

## Testes

`python -m unittest -v test_server`; `node --check public/app.js`; `docker build -t painelzinho-keile .`.
