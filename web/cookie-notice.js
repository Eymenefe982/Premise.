// Premise yalnızca zorunlu çerezler kullanır (oturum ve bedava kredinin toplu kayda karşı
// korunması); bunlar onay gerektirmez.
// Bu yüzden "kabul et / reddet" değil, bir kez gösterilen bir bilgilendirme.
(function () {
  var KEY = 'premise_cookie_notice';
  try { if (localStorage.getItem(KEY)) return; } catch (e) { return; }

  var bar = document.createElement('div');
  bar.setAttribute('role', 'region');
  bar.setAttribute('aria-label', 'Cookie notice');
  bar.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;' +
    'max-width:560px;margin:0 auto;display:flex;gap:14px;align-items:center;' +
    'padding:12px 14px;border-radius:10px;font:13.5px/1.5 system-ui,sans-serif;' +
    'background:#111a1d;color:#e8eef0;box-shadow:0 8px 30px rgba(0,0,0,.25)';

  var text = document.createElement('span');
  text.style.flex = '1';
  text.innerHTML = 'We only use essential cookies: to keep you signed in and to protect free ' +
    'credits from bulk sign-ups. No tracking or ' +
    'advertising cookies. <a href="/gizlilik#cookies" style="color:#7fd3c9">Learn more</a>';

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'OK';
  btn.style.cssText = 'flex:none;border:0;border-radius:8px;padding:8px 16px;cursor:pointer;' +
    'font:600 13.5px system-ui,sans-serif;background:#2dd4bf;color:#04201d';
  btn.addEventListener('click', function () {
    try { localStorage.setItem(KEY, '1'); } catch (e) {}
    bar.remove();
  });

  bar.appendChild(text);
  bar.appendChild(btn);
  (document.body || document.documentElement).appendChild(bar);
})();
