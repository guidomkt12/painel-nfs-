import {cpSync,rmSync,readFileSync,writeFileSync} from 'node:fs';
rmSync('dist',{recursive:true,force:true});
cpSync('public','dist',{recursive:true});
const html=readFileSync('dist/index.html','utf8');
if(!html.includes('<script defer src="/app.js"></script>'))throw new Error('Entrada do painel não localizada');
writeFileSync('dist/index.html',html.replace('<script defer src="/app.js"></script>','<script defer src="/demo-host.js"></script><script defer src="/app.js"></script>').replace('Ambiente privado','Demonstração • dados fictícios').replace('Registro compartilhado','Registro nesta sessão'));
cpSync('scripts/demo-host.js','dist/demo-host.js');