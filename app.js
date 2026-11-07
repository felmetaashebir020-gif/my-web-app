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
async function register(){try{let body={full_name:$('name').value,email:$('email').value,username:$('username').value,phone:$('phone').value,telegram_chat_id:$('telegramChatId').value,mt5_account:$('mt5').value,password:$('password').value};let j=await req('/api/register',{method:'POST',body:JSON.stringify(body)});window._regEmail=j.email||'';msg(j.message)}catch(e){msg(e.message)}}
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
loadEaInfo()}catch(e){msg(e.message)}}
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
  d.innerHTML='<b>Download EA (v'+i.version+')</b> <span class="muted">'+fmtSize(i.size)+'</span><br><button onclick="downloadEA()">Download EA v'+i.version+'</button>'+
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
