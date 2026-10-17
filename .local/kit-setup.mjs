import {setup} from '../external/codebound-kit/harness/setup.mjs';
const original=console.log;
console.log=(...args)=>{const s=args.join(' '); if(/^\n?▸/.test(s)) original(s);};
try { await setup({url:'http://127.0.0.1:4200',mode:'docker',appContainer:'codebound-kit-app'}); original('Official isolated setup completed. Credentials retained only in private state.'); }
catch(e){original('Official setup failed: '+e.name+' status '+(e.status||'unavailable'));process.exitCode=1;}

