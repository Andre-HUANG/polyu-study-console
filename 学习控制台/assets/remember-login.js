// Injected by the native login window only; configuration never contains a password.
(() => {
  'use strict';
  const config = __POLYU_REMEMBER_CONFIG__;
  if (!config.enabled || window !== window.top || location.origin !== 'https://adfs.polyu.edu.hk' || location.pathname !== '/adfs/ls/') return;
  const form = document.getElementById('loginForm');
  const username = document.getElementById('userNameInput');
  const password = document.getElementById('passwordInput');
  if (!form || !username || !password || username.form !== form || password.form !== form ||
      username.name !== 'UserName' || password.name !== 'Password' || password.type !== 'password' ||
      form.method.toLowerCase() !== 'post') return;
  const action = new URL(form.action, location.href);
  if (action.origin !== location.origin || action.pathname !== '/adfs/ls/') return;
  window.polyuRememberStop?.();
  const documentId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  const send = (kind, values = {}) => {
    const message = {type: 'polyuRemember', kind, nonce: config.nonce, documentId, ...values};
    if (config.platform === 'mac') window.webkit?.messageHandlers?.canvasCredentials?.postMessage(message);
    else window.chrome?.webview?.postMessage(message);
  };
  const capture = () => {
    if (!username.value || !password.value || username.value.length > 512 || password.value.length > 4096) return;
    send('candidate', {username: username.value, password: password.value});
  };
  // AD FS uses loginForm.submit(), which bypasses ordinary submit listeners.
  const originalSubmit = form.submit;
  const wrappedSubmit = function () { capture(); return originalSubmit.call(form); };
  form.submit = wrappedSubmit;
  form.addEventListener('submit', capture, true);
  window.polyuRememberFill = value => {
    if (location.origin !== 'https://adfs.polyu.edu.hk' || location.pathname !== '/adfs/ls/' ||
        value.nonce !== config.nonce || value.documentId !== documentId ||
        document.getElementById('loginForm') !== form || password.type !== 'password' ||
        (username.value && username.value !== value.username) || password.value) return false;
    username.value = value.username;
    password.value = value.password;
    username.dispatchEvent(new Event('input', {bubbles: true}));
    password.dispatchEvent(new Event('input', {bubbles: true}));
    return true;
  };
  window.polyuRememberStop = () => {
    if (form.submit === wrappedSubmit) form.submit = originalSubmit;
    form.removeEventListener('submit', capture, true);
    delete window.polyuRememberFill;
    delete window.polyuRememberStop;
  };
  send('ready');
})();
