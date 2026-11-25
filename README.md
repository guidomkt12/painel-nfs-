# Painelzinho da Keile

Aplicação independente para conferência de DDA. Python 3.12, SQLite persistente e eventos SSE. Nenhum dado financeiro real, foto pessoal ou credencial de produção é armazenado neste repositório público.

## Teste local

Execute `python demo.py` com Python 3.10+. Informe os dois e-mails de homologação quando solicitado. A senha de ambos é 123456. Abra http://127.0.0.1:8080. Clique em **Simular DDA** para receber três cobranças fictícias individualmente. O modo demo usa banco temporário, não aceita upload real ou webhook externo e não acessa Omie ou SharePoint.

## Produção

Hospede o Dockerfile da raiz em serviço privado, porta 8080, volume persistente /data gravável pelo UID 10001, proxy HTTPS e buffering SSE desabilitado. Configure variáveis de .env.example com credenciais únicas fortes (>=16 caracteres), URL HTTPS, operações permitidas, token webhook dedicado (>=32 caracteres), URL e token do n8n. Nunca habilite DEMO_MODE na produção. Configure backups, rate limit e acesso corporativo. Para múltiplas réplicas migre para PostgreSQL.

## Contrato de integração

POST /api/events com Authorization: Bearer <WEBHOOK_TOKEN>. Evento JSON com type, batch_id, event_id estável e revision inteira crescente. Tipos: batch.started (name,total), row.upsert (row_id,row), batch.completed (total), batch.failed (error). row inclui operacao, fornecedor, documento, cnpj, valor_centavos inteiro, vencimento, status, nota, detalhe, links[{label,url}]. Enviar cada resultado na conclusão de sua unidade de trabalho, antes de consolidar. Não inventar correspondências fiscais. IDs de lote e linha devem permanecer estáveis durante reenvio. Conferência humana é compartilhada e reaberta quando evidência muda.

Exemplo: {"type":"row.upsert","batch_id":"lote1","event_id":"lote1:linha1:2","revision":2,"row_id":"PAJ:12","row":{"operacao":"PAJ","fornecedor":"Fornecedor fictício","valor_centavos":28719,"status":"REVISAR"}}

Upload do painel envia JSON batch_id, filename, xlsx_base64 ao webhook configurado e exige ACK accepted:true e batch_id correspondente. Integração real requer configuração e homologação do n8n. Sem publicação automática no GitHub Pages: exige backend.

## Testes

`python -m unittest -v test_server`; `node --check public/app.js`; `docker build -t painelzinho-keile .`.
