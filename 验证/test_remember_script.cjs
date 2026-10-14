const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../学习控制台/assets/remember-login.js'), 'utf8');
let checks = 0;
const check = fn => { fn(); checks++; };
function fixture({origin = 'https://adfs.polyu.edu.hk', path = '/adfs/ls/', frame = false, enabled = true, invalid = false} = {}) {
  const messages = [], submissions = [];
  const username = {name:'UserName',value:'',dispatchEvent(){}};
  const password = {name:'Password',type:'password',value:'',dispatchEvent(){}};
  const listeners = {};
  const form = {method:'post',action:origin+path,submit(){submissions.push([username.value,password.value]);},
    addEventListener(name,cb){listeners[name]=cb;},removeEventListener(name,cb){if(listeners[name]===cb)delete listeners[name];}};
  username.form = password.form = form;
  const document = {getElementById(id){return id==='loginForm'?form:id==='userNameInput'?username:id==='passwordInput'?password:null;}};
  if (invalid) password.name = 'UnexpectedPassword';
  const window = {chrome:{webview:{postMessage(m){messages.push(m);}}}};window.top=frame?{}:window;
  const context = vm.createContext({window,document,location:{origin,pathname:path,href:origin+path},URL,Event:class Event {},crypto:{getRandomValues(a){a.fill(17);return a;}}});
  const configured = source.replace('__POLYU_REMEMBER_CONFIG__',JSON.stringify({platform:'windows',enabled,nonce:'a'.repeat(64)}));
  vm.runInContext(configured,context);
  return {window,username,password,form,messages,submissions,listeners,context};
}
for (const options of [{enabled:false},{origin:'https://evil.example'},{path:'/adfs/portal/updatepassword/'},{frame:true},{invalid:true}]) {
  const f=fixture(options);check(()=>assert.equal(f.messages.length,0));check(()=>assert.equal(f.window.polyuRememberFill,undefined));
}
const f=fixture();check(()=>assert.equal(f.messages[0].kind,'ready'));
const special = 'synthetic-only-' + String.fromCharCode(34,39) + '\\换行\n $() </script>';
const value = {nonce:'a'.repeat(64),documentId:f.messages[0].documentId,username:'synthetic-netid',password:special};
check(()=>assert.equal(f.window.polyuRememberFill({...value,nonce:'wrong'}),false));
check(()=>assert.equal(f.window.polyuRememberFill(value),true));
check(()=>assert.equal(f.password.type,'password'));
check(()=>assert.equal(f.submissions.length,0));
check(()=>assert.equal(f.messages.length,1));
f.form.submit();
check(()=>assert.equal(f.messages[1].kind,'candidate'));
check(()=>assert.equal(f.messages[1].password,special));
check(()=>assert.equal(f.submissions[0][1],special));
f.password.value = 'user-edited';check(()=>assert.equal(f.window.polyuRememberFill(value),false));
f.window.polyuRememberStop();f.form.submit();check(()=>assert.equal(f.messages.length,2));
const n=fixture();n.username.value='demo';n.password.value='synthetic-only';n.listeners.submit();
check(()=>assert.equal(n.messages[1].kind,'candidate'));
console.log('REMEMBER_SCRIPT_CHECKS='+checks);
