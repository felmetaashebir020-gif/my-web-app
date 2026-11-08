const $=id=>document.getElementById(id); const api=()=>$('api').value.replace(/\/$/,'');
function msg(x){$('msg').textContent=x; setTimeout(()=>$('msg').textContent='',5000)}
function show(id){document.querySelectorAll('.page').forEach(x=>x.classList.add('hidden'));$(id).classList.remove('hidden'); if(id==='signals')loadSignals(); if(id==='dash'&&!window.gmChartLoaded)loadTVChart('XAUUSD'); if(id==='admin'){const k=localStorage.getItem('gmAdminKey');if(k)$('adminKey').value=k}}
async function sendContact(){const n=$('contactName').value.trim(),t=$('contactTelegram').value.trim(),s=$('contactSubject').value.trim(),m=$('contactMessage').value.trim();if(!n||!t||!m){$('contactStatus').textContent='Please enter your name, Telegram username and message.';return}$('contactStatus').textContent='Sending…';try{let r=await fetch(api()+'/api/contact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,telegram:t,subject:s,message:m})});let x=await r.json();if(!r.ok)throw new Error(x.error||'Failed to send');$('contactStatus').textContent=x.message;$('contactMessage').value='';msg('Message sent')}catch(err){$('contactStatus').textContent=err.message}}
async function req(path,opt={}){let r=await fetch(api()+path,{headers:{'Content-Type':'application/json',...(localStorage.getItem('gmToken')?{Authorization:'Bearer '+localStorage.getItem('gmToken')}:{}),...(opt.headers||{})},...opt});let j=await r.json();if(!r.ok)throw new Error(j.error||'Request failed');return j}
async function health(){try{let j=await req('/api/health');msg('API online: '+j.time);loadSymbols()}catch(e){msg(e.message)}}
async function loadSymbols(){try{let j=await req('/api/market/symbols');$('symbols').innerHTML=j.symbols.map(s=>`<button onclick="quote('${s}')">${s}</button>`).join(' ');$('marketStatus').textContent='Provider: '+j.provider}catch(e){$('symbols').textContent=e.message}}
const tvMap={XAUUSD:'OANDA:XAUUSD',XAGUSD:'OANDA:XAGUSD',EURUSD:'OANDA:EURUSD',GBPUSD:'OANDA:GBPUSD',USDJPY:'OANDA:USDJPY',USDCHF:'OANDA:USDCHF',USDCAD:'OANDA:USDCAD',AUDUSD:'OANDA:AUDUSD',NZDUSD:'OANDA:NZDUSD',EURJPY:'OANDA:EURJPY',GBPJPY:'OANDA:GBPJPY',BTCUSD:'BITSTAMP:BTCUSD',ETHUSD:'BITSTAMP:ETHUSD',NAS100:'OANDA:NAS100USD',US30:'OANDA:US30USD'};
function loadTVChart(s){$('chartTitle').textContent='Live Chart — '+s; $('tvChart').innerHTML=''; window.gmChartLoaded=true; if(window.TradingView){new TradingView.widget({autosize:true,symbol:tvMap[s]||s,interval:'5',timezone:'Etc/UTC',theme:'dark',style:'1',locale:'en',toolbar_bg:'#f1f3f6',enable_publishing:false,allow_symbol_change:true,container_id:'tvChart'});} else {$('tvChart').textContent='Chart library loading…';}}
async function quote(s){loadTVChart(s);try{let j=await req('/api/market/quote/'+s);let q=j.quote;$('quoteBox').innerHTML='<b>'+j.symbol+'</b><br>Price: '+(q.close??'—')+'<br>Open: '+(q.open??'—')+'<br>High: '+(q.high??'—')+'<br>Low: '+(q.low??'—')+'<br>Change: '+(q.percent_change??'—')+'%<br>Time: '+(q.datetime??'—');$('marketStatus').textContent='Live provider: '+j.provider}catch(e){$('quoteBox').textContent=e.message;msg(e.message)}}
async function loadCalendar(country){try{let j=await req('/api/calendar?country='+encodeURIComponent(country));let ev=j.events||[];$('calendarList').innerHTML=ev.length?'<table><tr><th>Date</th><th>Country</th><th>Event</th><th>Importance</th><th>Actual</th><th>Forecast</th><th>Previous</th></tr>'+ev.map(x=>'<tr><td>'+((x.Date||x.date||'').toString().replace('T',' ').slice(0,19))+'</td><td>'+(x.Country||x.country||'')+'</td><td>'+(x.Event||x.event||'')+'</td><td>'+(x.Importance||x.importance||'')+'</td><td>'+(x.Actual??x.actual??'')+'</td><td>'+(x.Forecast??x.forecast??'')+'</td><td>'+(x.Previous??x.previous??'')+'</td></tr>').join('')+'</table>':'No events returned.';}catch(e){$('calendarList').textContent=e.message;msg(e.message)}}
async function register(){try{let body={ref:localStorage.getItem('gmRef')||'',full_name:$('name').value,email:$('email').value,username:$('username').value,phone:$('phone').value,telegram_chat_id:$('telegramChatId').value,mt5_account:$('mt5').value,password:$('password').value};let j=await req('/api/register',{method:'POST',body:JSON.stringify(body)});window._regEmail=j.email||'';msg(j.message)}catch(e){msg(e.message)}}
async function verifyOtp(){try{let j=await req('/api/verify-email',{method:'POST',body:JSON.stringify({email:$('email').value.trim()||window._regEmail||'',code:$('otp').value})});msg('Email verified. Status: '+j.status)}catch(e){msg(e.message)}}
async function login(){try{let j=await req('/api/login',{method:'POST',body:JSON.stringify({username_or_email:$('loginId').value,password:$('loginPw').value})});localStorage.setItem('gmEmail',j.user.email);localStorage.setItem('gmToken',j.token||'');$('loginOut').textContent=JSON.stringify(j.user,null,2);let b=$('licenseBox');b.classList.remove('hidden');b.innerHTML='<b>EA License Key</b><br>Paste this into the EA\'s <code>LicenseKey</code> input. It works only on MT5 account <code>'+j.user.mt5_account+'</code> (the EA reads the account number by itself):<br><code>'+j.user.license_key+'</code>';renderEaBox(b);msg('Login successful')}catch(e){msg(e.message)}}
async function setMembership(){try{let body={email:$('memEmail').value,membership:$('memTier').value,membership_expiry:$('memExpiry').value||null};let j=await req('/api/admin/membership',{method:'POST',headers:{'X-Admin-Key':$('adminKey').value},body:JSON.stringify(body)});$('memStatus').textContent='Saved: '+j.membership+(j.membership_expiry?(' until '+j.membership_expiry):'');msg('Membership updated')}catch(e){$('memStatus').textContent=e.message;msg(e.message)}}
const cryptoAddresses={BEP20:'0x8d1747458052facabe4ed43436a691ef70865b07',TRC20:'TY4vHjWgohGsfvh4PC95znuvp5ovM7Tph4'};
function updateDepositAddress(){const net=$('depNetwork').value;$('depAddress').textContent=cryptoAddresses[net]}
async function payment(kind){try{let amount=kind==='DEPOSIT'?$('depAmount').value:$('withAmount').value;let network=kind==='DEPOSIT'?$('depNetwork').value:$('withNetwork').value;let destination=kind==='DEPOSIT'?cryptoAddresses[network]:$('withDest').value;let body={email:$('payEmail').value,kind,amount,currency:'USD',method:network,reference:$('depRef').value,destination};let j=await req('/api/payment-request',{method:'POST',body:JSON.stringify(body)});msg(kind+' request: '+j.status)}catch(e){msg(e.message)}}
async function loadSignals(){try{let j=await req('/api/signals');if(j.error){$('signalList').innerHTML='<p class="muted">'+j.error+'</p><button onclick="goDeposit()">Deposit for membership</button>';return}let extra=(j.trial&&typeof j.trial_signals_remaining!=='undefined')?`<p class="muted">Free trial: ${j.trial_signals_remaining} signal(s) remaining.</p>`:'';$('signalList').innerHTML=extra+(j.signals.length?`<table><tr><th>Symbol</th><th>TF</th><th>Direction</th><th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>TP3</th><th>Setup score</th><th></th></tr>${j.signals.map(x=>`<tr><td>${x.symbol}</td><td>${x.timeframe??''}</td><td>${x.direction}</td><td>${x.entry??''}</td><td>${x.sl??''}</td><td>${x.tp1??''}</td><td>${x.tp2??''}</td><td>${x.tp3??''}</td><td>${x.confidence??''}</td><td><button onclick="goDeposit('${x.symbol} ${x.direction}${x.entry?' @ '+x.entry:''}')">Follow</button></td></tr>`).join('')}</table>`:'No active signals yet.')}catch(e){msg(e.message)}}
function botCell(x){
 if(!x.mt5_account) return '<span class="muted">-</span>';
 let paused=(x.bot_enabled===0||x.bot_enabled===false);
 let seen=x.bot_seen?Date.parse(x.bot_seen):0;
 let online=seen&&(Date.now()-seen<180000);
 let info=!seen?'<span class="muted">never connected</span>':((online?'ONLINE':'offline')+'<br><span class="muted">eq '+(x.bot_equity==null?'?':Number(x.bot_equity).toFixed(2))+' | pos '+(x.bot_positions||0)+(x.bot_version?' | v'+x.bot_version:'')+'</span>');
 return info+(paused?'<br><b>PAUSED</b> ':'<br>')+'<button onclick="adminBotControl(\''+x.mt5_account+'\','+(paused?'true':'false')+')">'+(paused?'Resume':'Pause')+'</button>';
}
async function adminBotControl(account,enabled){try{await req('/api/admin/bot-control',{method:'POST',headers:{'X-Admin-Key':$('adminKey').value||localStorage.getItem('gmAdminKey')||''},body:JSON.stringify({mt5_account:account,enabled})});msg('EA '+account+(enabled?' resumed':' paused'));loadAdmin()}catch(e){msg(e.message)}}
async function adminChangeMt5(email,old){let n=prompt('New MT5 account number for '+email+' (numbers only). Current: '+(old||'none'),old||'');if(n===null)return;n=n.trim();if(!n)return;try{await req('/api/admin/user-mt5',{method:'POST',headers:{'X-Admin-Key':$('adminKey').value||localStorage.getItem('gmAdminKey')||''},body:JSON.stringify({email:email,mt5_account:n})});msg('MT5 account changed to '+n);loadAdmin()}catch(e){msg(e.message)}}
async function loadAdmin(){let key=$('adminKey').value;localStorage.setItem('gmAdminKey',key);try{let [u,p]=await Promise.all([req('/api/admin/users',{headers:{'X-Admin-Key':key}}),req('/api/admin/payments',{headers:{'X-Admin-Key':key}})]);
$('adminUsers').innerHTML='<h3>Users</h3>'+(u.users.length?`<table><tr><th>Name</th><th>Telegram</th><th>MT5</th><th>Status</th><th>Membership</th><th>EA</th><th>Action</th></tr>${u.users.map(x=>`<tr><td>${x.full_name}<br><span class="muted">@${x.username}</span></td><td>${x.phone||''}<br><span class="muted">ID ${x.telegram_chat_id||'-'}</span></td><td>${x.mt5_account??''}</td><td>${x.status}</td><td>${x.membership}</td><td>${botCell(x)}</td><td>${x.status==='PENDING'?`<button onclick="adminUserStatus('${x.email}','APPROVED')">Approve</button><button onclick="adminUserStatus('${x.email}','REJECTED')">Reject</button>`:`<button onclick="adminUserStatus('${x.email}','SUSPENDED')">Suspend</button>`}<br><button onclick="adminChangeMt5(\'${x.email}\',\'${x.mt5_account||''}\')">Edit MT5</button></td></tr>`).join('')}</table>`:'No users yet.');
$('adminPayments').innerHTML='<h3>Payments</h3>'+(p.payments.length?`<table><tr><th>User</th><th>Kind</th><th>Amount</th><th>Method</th><th>Ref</th><th>Status</th><th>Action</th></tr>${p.payments.map(x=>`<tr><td>${x.full_name}<br><span class="muted">${x.email}</span></td><td>${x.kind}</td><td>${x.amount} ${x.currency}</td><td>${x.method||''}</td><td>${x.reference||''}</td><td>${x.status}</td><td>${x.status==='PENDING'?`<button onclick="adminPaymentStatus(${x.id},'APPROVED')">Approve</button><button onclick="adminPaymentStatus(${x.id},'REJECTED')">Reject</button>`:''}</td></tr>`).join('')}</table>`:'No payment requests yet.');
loadEaInfo();loadAdminMore()}catch(e){msg(e.message)}}
async function adminUserStatus(email,status){try{await req('/api/admin/user-status',{method:'POST',headers:{'X-Admin-Key':$('adminKey').value||localStorage.getItem('gmAdminKey')||''},body:JSON.stringify({email,status})});msg('User '+email+' -> '+status);loadAdmin()}catch(e){msg(e.message)}}
async function adminPaymentStatus(id,status){try{await req('/api/admin/payment-status',{method:'POST',headers:{'X-Admin-Key':$('adminKey').value||localStorage.getItem('gmAdminKey')||''},body:JSON.stringify({payment_id:id,status})});msg('Payment #'+id+' -> '+status);loadAdmin()}catch(e){msg(e.message)}}
loadSymbols(); setTimeout(()=>loadTVChart('XAUUSD'),300);

function fmtSize(n){return n>=1048576?(n/1048576).toFixed(2)+' MB':Math.round(n/1024)+' KB'}
async function renderEaBox(lb){
 try{
  let d=document.getElementById('eaBox');if(d)d.remove();
  d=document.createElement('div');d.id='eaBox';d.style.marginTop='14px';lb.appendChild(d);
  let i=await req('/api/ea/info');
  if(!i.available){d.innerHTML='<b>Download EA</b><br><span class="muted">The EA file is not available yet. Please check back soon.</span>';return}
  let upd=i.update_available?'<div style="background:#3a2d00;border:1px solid #d4a800;color:#ffe08a;border-radius:8px;padding:8px 10px;margin:8px 0">⚠️ <b>Update available:</b> v'+i.version+(i.your_version?' (you are using v'+i.your_version+')':'')+'. Download it below, replace the old file in MQL5/Experts and restart MT5.</div>':(i.your_version?'<div class="muted" style="margin:6px 0">✅ You are using the latest version (v'+i.your_version+').</div>':'');
  d.innerHTML=upd+'<b>Download EA (v'+i.version+')</b> <span class="muted">'+fmtSize(i.size)+'</span><br><button onclick="downloadEA()">Download EA v'+i.version+'</button>'+
   '<ol class="muted" style="font-size:13px;line-height:1.6;padding-left:18px"><li>Save the file, then in MT5 open <b>File &rarr; Open Data Folder &rarr; MQL5 &rarr; Experts</b> and paste it there.</li><li>Restart MT5 (or right-click Experts in the Navigator &rarr; Refresh).</li><li><b>Tools &rarr; Options &rarr; Expert Advisors</b>: tick <i>Allow WebRequest</i> and add this website\'s API URL.</li><li>Drag the EA onto an XAUUSD chart and paste your License Key into the <code>LicenseKey</code> input.</li></ol>'+
   '<p class="muted" style="font-size:12px">The EA only works with your License Key on your own MT5 account. Do not share the file or key.</p>';
 }catch(e){}
}
async function downloadEA(){
 try{
  let i=await req('/api/ea/info');
  let r=await fetch(api()+'/api/ea/download',{headers:{Authorization:'Bearer '+(localStorage.getItem('gmToken')||'')}});
  if(!r.ok){let j={};try{j=await r.json()}catch(x){}throw new Error(j.error||'Download failed')}
  let b=await r.blob();let a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=i.filename||'GOLD_MASTERS_EA.ex5';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),4000);msg('Download started')
 }catch(e){msg(e.message)}
}
async function uploadEA(){
 let key=$('adminKey').value||localStorage.getItem('gmAdminKey')||'',f=$('eaFile').files[0],v=$('eaVersion').value.trim();
 if(!f){$('eaStatus').textContent='Choose the compiled .ex5 file first.';return}
 if(!v){$('eaStatus').textContent='Enter a version number.';return}
 if(!/\.ex5$/i.test(f.name)){$('eaStatus').textContent='Only the .ex5 file is allowed (never upload .mq5).';return}
 let fd=new FormData();fd.append('file',f);fd.append('version',v);
 $('eaStatus').textContent='Uploading…';
 try{
  let r=await fetch(api()+'/api/admin/ea-upload',{method:'POST',headers:{'X-Admin-Key':key},body:fd});let j=await r.json();
  if(!r.ok)throw new Error(j.error||'Upload failed');
  $('eaStatus').textContent='Uploaded: '+j.filename+' v'+j.version+' ('+fmtSize(j.size)+')';msg('EA uploaded');loadEaInfo()
 }catch(e){$('eaStatus').textContent=e.message;msg(e.message)}
}
async function loadEaInfo(){
 try{
  let key=$('adminKey').value||localStorage.getItem('gmAdminKey')||'';
  let j=await req('/api/admin/ea-info',{headers:{'X-Admin-Key':key}});
  $('eaCurrent').textContent=j.available?('Current file: '+j.ea.filename+' v'+j.ea.version+' ('+fmtSize(j.ea.size)+'), uploaded '+j.ea.uploaded_at.slice(0,16).replace('T',' ')+' UTC. Total downloads: '+j.downloads):'No EA uploaded yet.'
 }catch(e){}
}

// ---------- News alerts (banner + optional browser notification) ----------
async function checkNews(){
 try{
  let j=await req('/api/news/alerts'),b=$('newsBanner'),a=j.alerts||[];
  if(!a.length){b.classList.add('hidden');b.innerHTML='';return}
  b.innerHTML=a.map(x=>{
   let when=x.status==='upcoming'?('in '+x.minutes+' min'):'just released';
   let extra=(x.status==='released'&&x.actual!=null&&x.actual!==0)?(' | Actual '+x.actual+(x.forecast?(' vs forecast '+x.forecast):'')):'';
   return '<div>⚠️ <b>High-impact news '+when+':</b> '+x.event+' (USD)'+extra+'. Expect fast price moves, trade carefully.</div>'
  }).join('');
  b.classList.remove('hidden');
  if('Notification' in window&&Notification.permission==='granted'){
   let seen=[];try{seen=JSON.parse(localStorage.getItem('gmNotified')||'[]')}catch(e){}
   a.filter(x=>x.status==='upcoming'&&x.minutes<=30).forEach(x=>{
    let k=x.event+'|'+x.time;if(seen.includes(k))return;seen.push(k);
    try{new Notification('GOLD MASTERS: news in '+x.minutes+' min',{body:x.event+' (USD) - high impact',icon:'logo.png'})}catch(e){}
   });
   try{localStorage.setItem('gmNotified',JSON.stringify(seen.slice(-50)))}catch(e){}
  }
 }catch(e){}
}
function enableNewsAlerts(){
 if(!('Notification' in window)){msg('Alerts show in the app banner. Your browser has no pop-up notifications.');return}
 Notification.requestPermission().then(p=>{msg(p==='granted'?'News notifications enabled':'Notifications blocked. The banner in the app still shows news.');checkNews()})
}
checkNews();setInterval(checkNews,60000);

// ---------- Forgot password (OTP via Telegram) ----------
async function sendResetCode(){
 let id=$('rstId').value.trim();if(!id){$('rstStatus').textContent='Enter your username or Telegram ID.';return}
 $('rstStatus').textContent='Sending…';
 try{let r=await fetch(api()+'/api/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:id})});let x=await r.json();if(!r.ok)throw new Error(x.error||'Failed');$('rstStatus').textContent=x.message}catch(e){$('rstStatus').textContent=e.message}
}
async function resetPassword(){
 let b={identifier:$('rstId').value.trim(),code:$('rstCode').value.trim(),new_password:$('rstPw').value};
 if(!b.identifier||!b.code||!b.new_password){$('rstStatus').textContent='Enter your username, the code and a new password.';return}
 try{let r=await fetch(api()+'/api/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});let x=await r.json();if(!r.ok)throw new Error(x.error||'Failed');$('rstStatus').textContent=x.message;$('rstCode').value='';$('rstPw').value='';msg('Password changed');setTimeout(()=>showAuth('login'),1200)}catch(e){$('rstStatus').textContent=e.message}
}

// ---------- Mobile tab bar, install prompt, AI brief ----------
(function(){
 const _show=show;
 window.show=function(id){_show(id);try{document.querySelectorAll('#tabbar button').forEach(b=>b.classList.toggle('active',b.dataset.p===id));if(id==='ai'){loadAI(false);loadBotMode()}if(id==='wallet')loadWallet();window.scrollTo(0,0)}catch(e){}};
 try{document.querySelector('#tabbar [data-p="dash"]').classList.add('active')}catch(e){}
})();
let _deferredInstall=null;
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();_deferredInstall=e;let b=$('installBtn');if(b)b.style.display='inline-block'});
window.addEventListener('appinstalled',()=>{_deferredInstall=null;let b=$('installBtn');if(b)b.style.display='none'});
async function installApp(){if(!_deferredInstall){msg('On iPhone: tap Share, then Add to Home Screen.');return}_deferredInstall.prompt();await _deferredInstall.userChoice;_deferredInstall=null;let b=$('installBtn');if(b)b.style.display='none'}
let _aiLang='en',_aiBusy=false,_aiLoaded=0;
function aiTile(label,val,cls){let d=document.createElement('div');d.className='aiTile '+(cls||'');let s=document.createElement('small');s.textContent=label;let b=document.createElement('b');b.textContent=val;d.appendChild(s);d.appendChild(b);return d}
async function loadAI(force){
 if(_aiBusy)return;if(!force&&_aiLoaded&&Date.now()-_aiLoaded<60000)return;
 const tok=localStorage.getItem('gmToken');
 if(!tok){$('aiText').textContent='Log in with an approved account to see the AI brief.';return}
 _aiBusy=true;$('aiText').textContent='Analysing the market…';
 try{
  let r=await fetch(api()+'/api/ai/brief?lang='+_aiLang,{headers:{Authorization:'Bearer '+tok}});let j=await r.json();
  if(!r.ok)throw new Error(j.error||'Could not load the brief');
  let f=j.facts||{},t=$('aiTiles');t.innerHTML='';
  if(f.price){
   t.appendChild(aiTile('XAUUSD',f.price));
   t.appendChild(aiTile('Trend (1h)',f.trend==='up'?'▲ Bullish':f.trend==='down'?'▼ Bearish':'◆ Mixed',f.trend==='up'?'up':f.trend==='down'?'down':''));
   t.appendChild(aiTile('RSI 14',f.rsi14));
   t.appendChild(aiTile('24h change',f.change_24h_pct+'%',f.change_24h_pct>=0?'up':'down'));
   t.appendChild(aiTile('24h high / low',f.high_24h+' / '+f.low_24h));
   t.appendChild(aiTile('ATR 14',f.atr14));
  }
  if(f.news&&f.news.length)f.news.forEach(n=>t.appendChild(aiTile('News',n.event+(n.in_minutes>0?' in '+n.in_minutes+' min':' now'))));
  $('aiText').textContent=j.text;
  let bd=$('aiBadge');bd.textContent=j.source==='ai'?'AI':'Auto';bd.classList.remove('hidden');
  $('aiMeta').textContent='Updated '+new Date(j.generated_at).toLocaleTimeString()+(j.source==='ai'?' • written by AI from the data above':' • rule-based summary');
  _aiLoaded=Date.now();window._aiRetry=0;
 }catch(e){
  let net=(e.message==='Failed to fetch'||e.name==='TypeError');
  if(net&&(window._aiRetry||0)<2){window._aiRetry=(window._aiRetry||0)+1;$('aiText').textContent='Server is waking up… retrying';_aiBusy=false;setTimeout(()=>loadAI(true),9000);return}
  window._aiRetry=0;$('aiText').textContent=net?'Could not reach the server. It may be starting up or busy. Please tap Refresh in a minute.':e.message
 }
 _aiBusy=false;
}

// ---------- Bot mode switches (AUTO / MANUAL) ----------
let _modeTimer=null;
function modeFromState(s){s=(s||'').toUpperCase();return s.indexOf('AUTO')>=0?'AUTO':s.indexOf('MANUAL')>=0?'MANUAL':'OFF'}
async function loadBotMode(){
 const tok=localStorage.getItem('gmToken'),sa=$('swAuto'),sm=$('swManual');
 if(!tok){$('modeState').textContent='Log in with an approved account to control your bot.';sa.disabled=sm.disabled=true;return}
 try{
  let r=await fetch(api()+'/api/bot/mode',{headers:{Authorization:'Bearer '+tok}});let j=await r.json();
  if(!r.ok)throw new Error(j.error||'Could not load bot mode');
  let cur=j.online?modeFromState(j.state):j.requested;
  sa.checked=(cur==='AUTO');sm.checked=(cur==='MANUAL');
  sa.disabled=!j.auto_allowed||j.paused;sm.disabled=j.paused;
  let st=j.paused?'⛔ Paused by admin':(j.online?('🟢 EA online'+(j.version?' (v'+j.version+')':'')+' • mode: '+modeFromState(j.state)):'⚪ EA offline. Showing your last request: '+j.requested);
  if(j.online&&j.equity!=null)st+=' • equity '+Number(j.equity).toFixed(2);
  $('modeState').textContent=st;
  $('modeNote').textContent=!j.auto_allowed?('AUTO MODE is included in: '+(j.auto_plans||'a higher plan')+'.'):(j.online&&j.requested!==cur?'Waiting for the EA to apply: '+j.requested+' …':'');
  clearTimeout(_modeTimer);if(j.online&&j.requested!==cur)_modeTimer=setTimeout(loadBotMode,15000);
 }catch(e){$('modeState').textContent=(e.message==='Failed to fetch')?'Could not reach the server. Try again in a minute.':e.message}
}
async function toggleMode(which,el){
 const tok=localStorage.getItem('gmToken');if(!tok){el.checked=!el.checked;return}
 let mode=el.checked?which:'OFF';
 if(mode==='AUTO'&&!confirm('Turn AUTO MODE ON?\n\nThe EA will place REAL orders on your MT5 account when a fully confirmed signal appears. Use your own lot size and risk limits. Test on a demo account first.\n\nContinue?')){el.checked=false;return}
 $('swAuto').checked=(mode==='AUTO');$('swManual').checked=(mode==='MANUAL');
 try{
  let r=await fetch(api()+'/api/bot/mode',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tok},body:JSON.stringify({mode:mode})});let j=await r.json();
  if(!r.ok)throw new Error(j.error||'Failed');
  msg(j.message);$('modeNote').textContent='Request sent: '+mode+'. Waiting for the EA…';clearTimeout(_modeTimer);_modeTimer=setTimeout(loadBotMode,20000)
 }catch(e){msg(e.message);loadBotMode()}
}

// ---------- Membership levels (loaded from the server, so prices and AUTO access always match) ----------
async function loadPricing(){
 const g=$('priceGrid');if(!g)return;
 try{
  let r=await fetch(api()+'/api/pricing');let j=await r.json();if(!r.ok||!j.plans)throw new Error('x');
  fillOrderPlans(j.plans);g.innerHTML='';
  let t=document.createElement('div');t.className='priceBox';
  t.innerHTML='<b>Free Trial</b><span>3 days or 3 signals</span><ul><li>Daily gold signals</li><li>Entry, SL & TP</li><li>Basic dashboard</li></ul>';g.appendChild(t);
  j.plans.forEach(pl=>{
   let d=document.createElement('div');d.className='priceBox pick '+pl.code.toLowerCase();d.setAttribute('role','button');d.tabIndex=0;
   let go=()=>startOrder(pl.code);d.onclick=go;d.onkeydown=e=>{if(e.key==='Enter')go()};
   let b=document.createElement('b');b.textContent=pl.name;let s=document.createElement('span');s.textContent='$'+pl.price+' / '+pl.duration;
   let ul=document.createElement('ul');pl.features.forEach(f=>{let li=document.createElement('li');li.textContent=f;ul.appendChild(li)});
   let a=document.createElement('li');a.className=pl.auto?'autoYes':'autoNo';a.textContent=pl.auto?'✓ AUTO trading (EA)':'Signals only (MANUAL)';ul.appendChild(a);
   d.appendChild(b);d.appendChild(s);d.appendChild(ul);g.appendChild(d)
  })
 }catch(e){g.textContent='Plans could not be loaded. Please refresh.'}
}
loadPricing();

// ---------- Wallet, orders and referrals ----------
const escH=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>'$'+(Number(n)||0).toFixed(2);
const fdate=s=>s?String(s).slice(0,16).replace('T',' '):'';
let _plans={},_wal=null;
(function(){let r=new URLSearchParams(location.search).get('ref');if(r){r=r.toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,20);if(r){try{localStorage.setItem('gmRef',r)}catch(e){}setTimeout(()=>{try{show('auth');showAuth('reg');msg('Referral code applied: '+r)}catch(e){}},400)}try{history.replaceState(null,'',location.pathname+location.hash)}catch(e){}}})();
function fillOrderPlans(plans){_plans={};let s=$('ordPlan');if(!s)return;s.innerHTML='';plans.forEach(pl=>{_plans[pl.code]=pl;let o=document.createElement('option');o.value=pl.code;o.textContent=pl.name+' - $'+pl.price+' / '+pl.duration;s.appendChild(o)});updateOrderCalc()}
function updateOrderCalc(){
 let pl=_plans[$('ordPlan').value];if(!pl)return;
 let bal=_wal?_wal.available:0,use=Math.max(0,Math.min(parseFloat($('ordWallet').value)||0,pl.price,bal)),crypto=Math.round((pl.price-use)*100)/100;
 $('ordCalc').textContent='Price $'+pl.price+'  |  from wallet '+money(use)+'  |  to send in USDT: '+money(crypto);
 $('ordCrypto').classList.toggle('hidden',crypto<=0);$('ordAddr').textContent=cryptoAddresses[$('ordNet').value]||''
}
function fillMaxWallet(){let pl=_plans[$('ordPlan').value];if(!pl||!_wal)return;$('ordWallet').value=Math.min(pl.price,_wal.available).toFixed(2);updateOrderCalc()}
function startOrder(code){show('wallet');if(_plans[code]){$('ordPlan').value=code;updateOrderCalc()}setTimeout(()=>{let c=$('orderCard');if(c)c.scrollIntoView({behavior:'smooth'})},250)}
async function submitOrder(){
 const tok=localStorage.getItem('gmToken');if(!tok){$('ordStatus').textContent='Please log in first.';return}
 let pl=_plans[$('ordPlan').value];if(!pl)return;
 let b={plan:pl.code,wallet_amount:parseFloat($('ordWallet').value)||0,network:$('ordNet').value,txid:$('ordTx').value.trim()};
 $('ordStatus').textContent='Sending…';
 try{let r=await fetch(api()+'/api/order',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tok},body:JSON.stringify(b)});let j=await r.json();if(!r.ok)throw new Error(j.error||'Failed');$('ordStatus').textContent=j.message;msg(j.message);$('ordTx').value='';$('ordWallet').value='';loadWallet()}
 catch(e){$('ordStatus').textContent=e.message}
}
function tbl(head,rowsArr){return rowsArr.length?'<div style="overflow-x:auto"><table><tr>'+head.map(h=>'<th>'+h+'</th>').join('')+'</tr>'+rowsArr.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</table></div>':'<p class="muted">Nothing yet.</p>'}
function copyRefLink(){let t=$('refLink').value;if(navigator.clipboard)navigator.clipboard.writeText(t).then(()=>msg('Link copied'),()=>{$('refLink').select();msg('Press copy on your keyboard')});else{$('refLink').select();document.execCommand('copy');msg('Link copied')}}
async function loadWallet(){
 const tok=localStorage.getItem('gmToken');
 if(!tok){$('walTiles').innerHTML='';$('walNote').textContent='Log in to see your wallet.';$('refBody').textContent='Log in to see your referral link.';return}
 try{
  let r=await fetch(api()+'/api/wallet',{headers:{Authorization:'Bearer '+tok}});let j=await r.json();if(!r.ok)throw new Error(j.error||'Could not load wallet');_wal=j;
  let t=$('walTiles');t.innerHTML='';
  [['Available',money(j.available)],['Pending commission',money(j.pending_commission)],['Total earned',money(j.total_earned)],['Total spent',money(j.total_spent)],['Withdrawable',money(j.withdrawable)]].forEach(x=>t.appendChild(aiTile(x[0],x[1])));
  if(j.on_hold>0)t.appendChild(aiTile('On hold (order)',money(j.on_hold)));
  $('walNote').textContent='Plan: '+j.membership+(j.membership_expiry?' (until '+j.membership_expiry.slice(0,10)+')':'');
  let rb='';
  if(j.is_partner){
   rb='<p>You are a <b>Referral Partner</b>. Share your link. When someone registers with it and buys a membership, you earn <b>'+j.rate+'%</b> after the admin approves the payment.</p><input id="refLink" readonly value="'+escH(j.referral_link)+'"><button onclick="copyRefLink()">Copy link</button><p class="muted">Code: <b>'+escH(j.referral_code)+'</b></p>';
  }else{
   rb='<p>Platinum and Diamond members become <b>Referral Partners</b>, get a personal link and earn <b>'+j.rate+'%</b> commission on every membership their referrals buy.</p><button onclick="startOrder(\'PLATINUM\')">Become a partner (Platinum)</button>';
  }
  let rate=Object.values(_plans).map(pl=>[escH(pl.name),'$'+pl.price,money(pl.price*j.rate/100)]);
  rb+='<h3>Commission table</h3>'+tbl(['Package','Price','Your '+j.rate+'%'],rate)+'<p class="muted" style="font-size:12px">Free trials and self-referrals earn nothing, and each purchase pays only once. The part of a purchase paid with commission credits earns no commission. Commissions are credited to your wallet after admin approval.</p>';
  $('refBody').innerHTML=rb;
  $('walHist').innerHTML='<h3>My referrals</h3>'+tbl(['Name','Status','Plan','Joined'],j.referrals.map(x=>[escH(x.full_name),escH(x.status),escH(x.membership),fdate(x.created_at)]))+
   '<h3>Commissions</h3>'+tbl(['Plan','Basis','Amount','Status','Date'],j.commissions.map(x=>[escH(x.plan),money(x.basis),money(x.amount),escH(x.status),fdate(x.created_at)]))+
   '<h3>Orders</h3>'+tbl(['#','Plan','Price','Wallet','Crypto','Status','Date'],j.orders.map(x=>[x.id,escH(x.plan),money(x.price),money(x.wallet_amount),money(x.crypto_amount),escH(x.status),fdate(x.created_at)]))+
   '<h3>Wallet transactions</h3>'+tbl(['Date','Type','Amount','Balance','Note'],j.ledger.map(x=>[fdate(x.created_at),escH(x.kind),(x.amount>0?'+':'')+Number(x.amount).toFixed(2),money(x.balance_after),escH(x.note||'')]));
  updateOrderCalc()
 }catch(e){$('walNote').textContent=(e.message==='Failed to fetch')?'Could not reach the server. Try again in a minute.':e.message}
}
// ---------- Admin: orders, commissions, wallet adjustment, audit ----------
const admH=()=>({'X-Admin-Key':$('adminKey').value||localStorage.getItem('gmAdminKey')||''});
async function loadAdminMore(){
 try{
  let [o,c]=await Promise.all([req('/api/admin/orders',{headers:admH()}),req('/api/admin/commissions',{headers:admH()})]);
  $('adminOrders').innerHTML='<h3>Membership orders</h3>'+tbl(['#','User','Plan','Price','Wallet','Crypto','Network / TXID','Status','Action'],o.orders.map(x=>[x.id,escH(x.full_name)+'<br><span class="muted">@'+escH(x.username)+' '+escH(x.phone||'')+'</span>',escH(x.plan),money(x.price),money(x.wallet_amount),money(x.crypto_amount),escH(x.network||'')+'<br><code style="word-break:break-all;font-size:11px">'+escH(x.txid||'')+'</code>',escH(x.status),x.status==='PENDING'?'<button onclick="adminOrderStatus('+x.id+',\'APPROVED\')">Approve & Unlock</button><button onclick="adminOrderStatus('+x.id+',\'REJECTED\')">Reject</button>':'']));
  $('adminCommissions').innerHTML='<h3>Referral commissions</h3>'+tbl(['#','Referrer','Buyer','Plan','Basis','25%','Status','Action'],c.commissions.map(x=>[x.id,escH(x.referrer)+'<br><span class="muted">@'+escH(x.referrer_user)+'</span>',escH(x.buyer),escH(x.plan),money(x.basis),money(x.amount),escH(x.status),x.status==='PENDING'?'<button onclick="adminCommissionStatus('+x.id+',\'APPROVED\')">Credit</button><button onclick="adminCommissionStatus('+x.id+',\'REJECTED\')">Reject</button>':'']))+
   '<h3>Referral partners</h3>'+tbl(['Name','Plan','Expires','Code','Referred'],c.partners.map(x=>[escH(x.full_name)+'<br><span class="muted">@'+escH(x.username)+'</span>',escH(x.membership),escH((x.membership_expiry||'lifetime').slice(0,10)),escH(x.referral_code||'-'),x.referred]));
 }catch(e){}
}
async function adminOrderStatus(id,status){
 if(status==='APPROVED'&&!confirm('Have you verified this transaction on the blockchain (right network, right amount, your address)?'))return;
 try{await req('/api/admin/order-status',{method:'POST',headers:admH(),body:JSON.stringify({order_id:id,status:status})});msg('Order '+status.toLowerCase());loadAdminMore();loadAdmin()}catch(e){msg(e.message)}
}
async function adminCommissionStatus(id,status){try{await req('/api/admin/commission-status',{method:'POST',headers:admH(),body:JSON.stringify({id:id,status:status})});msg('Commission '+(status==='APPROVED'?'credited':'rejected'));loadAdminMore()}catch(e){msg(e.message)}}
async function adminWalletAdjust(){
 let b={user:$('adjUser').value.trim(),amount:$('adjAmount').value,note:$('adjNote').value.trim()};
 if(!b.user||!b.amount||!b.note){msg('Enter user, amount and reason');return}
 if(!confirm('Apply '+b.amount+' USD to '+b.user+'?'))return;
 try{await req('/api/admin/wallet-adjust',{method:'POST',headers:admH(),body:JSON.stringify(b)});msg('Wallet updated');$('adjAmount').value='';$('adjNote').value=''}catch(e){msg(e.message)}
}
async function loadAudit(){try{let j=await req('/api/admin/audit',{headers:admH()});$('adminAudit').innerHTML=tbl(['Time','Action','Detail'],j.log.map(x=>[fdate(x.created_at),escH(x.action),escH(x.detail)]))}catch(e){msg(e.message)}}
