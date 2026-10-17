import {setup} from '../external/codebound-kit/harness/setup.mjs';
const log=console.log;
console.log=(...args)=>{const s=args.join(' ');if(/^\n?▸/.test(s))log(s);};
try {await setup({url:'http://127.0.0.1:4200',mode:'docker',appContainer:'codebound-kit-app'});log('Official isolated setup completed; credentials saved privately, not printed.');}
catch(e){log('Official setup failed ('+e.name+'); inspect isolated instance state without publishing credentials.');process.exitCode=1;}
