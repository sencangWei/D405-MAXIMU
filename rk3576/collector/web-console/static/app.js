'use strict';
const $ = id => document.getElementById(id);
let token = '', state = null, actionBusy = false, previewBusy = false, previewId = null;
let renewing = false, pollBusy = false, startRequestId = null, toastTimer, clockBase = null, pendingPreviewJob = null, tableSignature = null, previewOpenedAt = 0, incompleteSignature = null, pendingRecover = null;
const activeStates = new Set(['starting', 'recording', 'stop_requested', 'finalizing']);
const stateNames = {idle:'待采集',starting:'正在启动',recording:'采集中',stop_requested:'正在停止',finalizing:'正在保存',complete_local:'已完成',interrupted:'采集中断',incomplete:'保存未完成'};
const phaseNames = {idle:'设备就绪，等待开始',starting:'正在准备相机与传感器',recording:'图像与传感器数据正在记录',stop_requested:'正在停止采集，请等待封存',finalizing:'正在校验并保存录制',complete_local:'保存完成，可以下载到本机',interrupted:'请检查设备状态后重试',incomplete:'录制未完整保存，请检查设备'};

function requestUUID() {
  const value=crypto.getRandomValues(new Uint8Array(16));value[6]=(value[6]&15)|64;value[8]=(value[8]&63)|128;
  const hex=Array.from(value,x=>x.toString(16).padStart(2,'0')).join('');return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
function toast(message, error=false) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true,error?8500:4000); }
async function api(path, body) { const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),50000);try {const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-UMI-Token':token},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal,cache:'no-store'});const result=await response.json();if(!response.ok)throw new Error(result.error||'设备请求失败');return result;}catch(e){if(e.name==='AbortError')throw new Error('操作响应超时，请先查看设备状态，再重试。');throw e;}finally{clearTimeout(timeout);} }
function bytes(n) { if(!Number.isFinite(n))return '—';if(n<1024)return n+' B';const units=['KB','MB','GB','TB'];let i=-1;do {n/=1024;i++;}while(n>=1024&&i<3);return n.toFixed(n>=100?0:1)+' '+units[i]; }
function duration(n) { n=Math.max(0,Math.floor(n||0));return [Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(x=>String(x).padStart(2,'0')).join(':'); }
function setClock(value) { const parts=duration(value).split(':');$('elapsed').replaceChildren();parts.forEach((part,i)=>{if(i){const span=document.createElement('span');span.textContent=':';$('elapsed').append(span);}$('elapsed').append(document.createTextNode(part));}); }
function text(tag,value,cls) { const element=document.createElement(tag);element.textContent=value;if(cls)element.className=cls;return element; }

function render() {
  if(!state)return;
  const connected=state.connected, capture=state.status||{}, phase=capture.state||'idle', active=activeStates.has(phase);
  $('connection').classList.toggle('online',connected);$('connectionLabel').textContent=connected?'设备已连接':'设备未连接';
  $('deviceAddress').textContent=state.host||'192.168.113.161';$('deviceId').textContent=state.identity?.device_id||'UMI-D405-RK3576';$('version').textContent=state.identity?.controller_version?.replace('-umi','')||'—';
  $('stateBadge').textContent=connected?(stateNames[phase]||phase):'未连接';$('stateBadge').className='state-badge'+(phase==='recording'?' recording':phase==='complete_local'?' complete':'');
  $('phaseText').textContent=connected?(phaseNames[phase]||'等待设备状态'):'检查设备电源和网络连接';
  const clockNow=performance.now();clockBase=UMIClock.reconcile(clockBase,capture,connected,clockNow);
  setClock(connected?UMIClock.value(clockBase,clockNow):UMIClock.sampleSeconds(capture));
  $('startButton').disabled=!connected||actionBusy||active||!state.camera_present||!state.serial_present;
  $('stopButton').disabled=!connected||actionBusy||!capture.job_id||!['recording','starting'].includes(phase);
  $('duration').disabled=actionBusy||active;
  $('previewToggle').disabled=!connected||previewBusy||actionBusy||!!pendingPreviewJob;$('previewCenterButton').disabled=$('previewToggle').disabled;
  $('recordIndicator').hidden=!capture.capture_running;
  $('cameraState').textContent=connected?(state.camera_present?'已识别':'未发现'):'—';$('serialState').textContent=connected?(state.serial_present?'已连接':'未发现'):'—';
  const free=connected&&state.disk?bytes(state.disk.free):'— GB';const [amount,unit]=free.split(' ');$('diskFree').replaceChildren(document.createTextNode(amount),text('small',unit||'GB'));
  $('storageFill').style.width=state.disk?`${Math.max(0,Math.min(100,100*(1-state.disk.free/state.disk.total)))}%`:'0%';
  $('temperature').replaceChildren(document.createTextNode(connected&&state.temperature!=null?state.temperature.toFixed(1):'—'),text('small','°C'));
  $('temperatureNote').textContent=connected?'当前最高温区':'等待设备数据';
  $('destinationPath').textContent=state.download_dir||'正在读取保存位置…';$('destinationPath').title=state.download_dir||'';
  const message=!connected?state.error:((phase==='interrupted'||phase==='incomplete')?(capture.error_detail||'上一次采集未完成。'):'');
  $('alert').hidden=!message;$('alert').textContent=readableError(message||'');
  $('lastUpdated').textContent=connected?'最后同步 '+new Date(state.updated_at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'正在等待设备';
  if(previewId){$('previewCounter').textContent=`已输出 ${Number(state.preview_frames||0).toLocaleString()} 帧`;}
  if(previewId&&!previewBusy&&!actionBusy&&state.owned_preview!==previewId&&state.updated_at*1000>=previewOpenedAt){previewId=null;$('previewImage').removeAttribute('src');previewUI('idle');}
  renderRecordings();
  renderIncomplete();
}

function readableError(message) {
  if(message.includes('camera busy:'))return '相机正在被其他预览或录制进程使用，请结束该任务后重试。';
  if(message.includes('STM32 warmup failed:'))return 'IMU 或编码器的实际数据预检未通过，本次采集未开始。详细原因已记录到日志。';
  if(message.includes('STM32 integrity or rate check failed'))return '传感器数据完整性检查未通过，本次录制未保存为完整记录。详细计数已记录到日志。';
  if(message.includes('encoder input drain timed out')||message.includes('encoder shutdown timed out'))return '视频编码器未能按时结束，本次录制未完成。详细原因已记录到日志。';
  if(message.includes('D405 continuity check failed'))return '相机数据连续性检查失败，本次录制未保存为完整记录。详细原因已记录到日志。';
  if(message.includes('Frame did not arrive in time'))return '相机未按时返回画面，本次采集中断。详细原因已记录到日志。';
  return message;
}

function renderRecordings() {
  const signature=JSON.stringify([state.recordings,state.transfers,state.deletions,state.connected]);if(signature===tableSignature)return;tableSignature=signature;
  const records=state.recordings||[], tasks=new Map((state.transfers||[]).map(t=>[t.recording_id,t])), deletions=new Map((state.deletions?.operations||[]).map(t=>[t.recording_id,t]));
  $('recordingCount').textContent=records.length;$('emptyState').hidden=records.length>0;$('recordingTableWrap').hidden=!records.length;
  const fragment=document.createDocumentFragment();
  records.forEach(record=>{
    const row=document.createElement('tr'), titleCell=document.createElement('td'),sizeCell=document.createElement('td'), statusCell=document.createElement('td'),actionCell=document.createElement('td');
    const date=new Date(record.recorded_at);titleCell.append(text('div',Number.isNaN(date.getTime())?'已完成录制':date.toLocaleString('zh-CN',{hour12:false}),'record-name'),text('span',record.recording_id,'record-id'));
    sizeCell.append(text('div',duration(record.duration_ms/1000)),text('div',bytes(record.total_bytes),'record-secondary'));
    const task=tasks.get(record.recording_id), deletion=deletions.get(record.recording_id), names={preparing:'准备下载包',copying:'正在打包',verifying:'正在校验',complete:'下载包已就绪',failed:'转存未完成'};
    const deleting=deletion&&['starting','deleting'].includes(deletion.state), deleteFailed=deletion?.state==='failed';
    statusCell.append(text('div',deleting?'正在安全删除':deleteFailed?'删除未完成':task?names[task.state]||task.state:'设备已保存','row-state'+(task?.state==='failed'||deleteFailed?' failed':'')));
    if(task&&['preparing','copying','verifying'].includes(task.state)){const track=text('div','','progress'),fill=document.createElement('div');const percent=task.total_bytes?100*task.bytes_done/task.total_bytes:0;fill.style.width=`${Math.min(100,percent)}%`;track.append(fill);statusCell.append(track,text('div',`${Math.floor(percent)}% · ${bytes(task.bytes_done)} / ${bytes(task.total_bytes)}`,'record-secondary'));}
    if(task?.error)statusCell.append(text('div',task.error,'transfer-error'));
    if(deletion?.error)statusCell.append(text('div',deletion.error,'transfer-error'));
    const actions=text('div','','row-actions'),button=text('button',task?.state==='complete'?'下载录制':task?.state==='failed'?'重试转存':task?'准备中…':'准备转存','transfer-button'+(task?.state==='complete'?' secondary':''));
    button.disabled=deleting||(task?.state==='complete'?false:(!state.connected||!!task&&task.state!=='failed'));button.dataset.recordingId=record.recording_id;
    button.addEventListener('click',async()=>{
      if(task?.state==='complete'){
        const link=document.createElement('a');link.href='/api/downloads/'+record.recording_id+'.zip';link.download=record.recording_id+'.zip';document.body.append(link);link.click();link.remove();toast('已交给浏览器下载，请在下载列表查看进度。');return;
      }
      button.disabled=true;
      try{await api('/api/transfers',{recording_id:record.recording_id});toast('正在准备并校验下载包，就绪后点击「下载录制」。');await poll();}
      catch(error){toast(error.message,true);}finally{button.disabled=false;}
    });
    const deleteButton=text('button',deleting?'删除中…':deleteFailed?'重试删除':'删除数据','transfer-button delete-button');
    deleteButton.disabled=!state.connected||deleting||!!task&&['preparing','copying','verifying'].includes(task.state);
    deleteButton.addEventListener('click',async()=>{if(!confirm(`确定永久删除这条录制吗？\n\n${record.recording_id}\n\n设备上的原始数据和已准备的下载包都会删除，且无法恢复。`))return;deleteButton.disabled=true;try{await api('/api/delete-recording',{recording_id:record.recording_id,request_id:requestUUID()});toast('删除请求已受理，正在核验并删除数据。');await poll();}catch(error){toast(error.message,true);}finally{deleteButton.disabled=false;}});
    actions.append(button,deleteButton);actionCell.append(actions);row.append(titleCell,sizeCell,statusCell,actionCell);fragment.append(row);
  });
  $('recordingRows').replaceChildren(fragment);
}

const kindNames = {unsealed:'未封存（断电或中断）',sealed_unpublished:'已封存·待登记'};
const recoveryNames = {starting:'正在准备',recovering:'正在抢救',complete:'抢救完成',failed:'抢救未完成'};
const incompleteDeleteNames = {starting:'正在准备删除',deleting:'正在删除',complete:'已删除',failed:'删除未完成'};

function renderIncomplete() {
  const data=state.incomplete||{}, sessions=data.sessions||[], operations=state.incomplete_operations||[];
  const signature=JSON.stringify([sessions,operations,state.connected,state.status?.state,state.identity?.controller_version]);if(signature===incompleteSignature)return;incompleteSignature=signature;
  const ops=new Map(operations.map(item=>[item.session,item]));
  $('incompleteCard').hidden=!sessions.length;
  $('incompleteCount').textContent=sessions.length;
  const capturing=activeStates.has(state.status?.state||'idle');
  $('incompleteNote').textContent=capturing&&sessions.length?'采集进行中：正在写入的数据受保护，不能抢救或删除；更早的中断数据仍可处理。':'';
  const fragment=document.createDocumentFragment();
  sessions.forEach(item=>{
    const row=document.createElement('tr'), titleCell=document.createElement('td'), sizeCell=document.createElement('td'), statusCell=document.createElement('td'), actionCell=document.createElement('td');
    const stamp=item.session.match(/-(\d{8}T\d{6}Z)-/);
    const when=stamp?`${stamp[1].slice(0,4)}-${stamp[1].slice(4,6)}-${stamp[1].slice(6,8)} ${stamp[1].slice(9,11)}:${stamp[1].slice(11,13)}:${stamp[1].slice(13,15)}`:'中断的采集';
    titleCell.append(text('div',when,'record-name'),text('span',item.session,'record-id'));
    sizeCell.append(text('div',item.estimated_duration_s?duration(item.estimated_duration_s):'时长未知'),text('div',bytes(item.total_bytes),'record-secondary'));
    const recovery=item.recovery||ops.get(item.session)||{}, deletion=item.deletion||{};
    const recovering=['starting','recovering'].includes(recovery.state), deleting=['starting','deleting'].includes(deletion.state);
    const failed=recovery.state==='failed'||deletion.state==='failed';
    let statusText=recovering?(recoveryNames[recovery.state]||recovery.state):deleting?(incompleteDeleteNames[deletion.state]||deletion.state):item.live?'正在写入·不可操作':item.already_published?'已抢救为本地录制':(kindNames[item.kind]||item.kind);
    statusCell.append(text('div',statusText,'row-state'+(failed?' failed':'')));
    if(recovering&&recovery.total_bytes){const track=text('div','','progress'),fill=document.createElement('div');const percent=recovery.total_bytes?100*(recovery.bytes_done||0)/recovery.total_bytes:0;fill.style.width=`${Math.min(100,percent)}%`;track.append(fill);statusCell.append(track,text('div',`${Math.floor(percent)}% · ${bytes(recovery.bytes_done||0)} / ${bytes(recovery.total_bytes)}`,'record-secondary'));}
    if(recovery.state==='complete')statusCell.append(text('div',`已入库为 ${recovery.recording_id||'本地录制'}${recovery.leftover_bytes?`；剩余未抢救数据 ${bytes(recovery.leftover_bytes)}`:''}`,'record-secondary'));
    if(recovery.error)statusCell.append(text('div',recovery.error,'transfer-error'));
    if(deletion.error)statusCell.append(text('div',deletion.error,'transfer-error'));
    const actions=text('div','','row-actions');
    const recoverButton=text('button','抢救数据','transfer-button');
    const space=item.space||{}, free=data.free_bytes||0;
    const affordable=sel=>(space[`required_${sel}`]||0)<=free;
    recoverButton.disabled=!state.connected||recovering||deleting||item.live||item.already_published||recovery.state==='complete'||!affordable('all')&&!affordable('ir')&&!affordable('rgb');
    recoverButton.addEventListener('click',()=>openRecoverPanel(item));
    const deleteButton=text('button',deleting?'删除中…':failed?'重试删除':'删除数据','transfer-button delete-button');
    deleteButton.disabled=!state.connected||item.live||recovering||deleting;
    deleteButton.addEventListener('click',async()=>{if(!confirm(`确定永久删除这条未完成的录制吗？\n\n${item.session}\n共 ${bytes(item.total_bytes)}\n\n删除后无法再抢救，也无法恢复。`))return;deleteButton.disabled=true;try{await api('/api/incomplete-delete',{session:item.session,request_id:requestUUID()});toast('删除请求已受理，正在回收空间。');await poll();}catch(error){toast(error.message,true);}finally{deleteButton.disabled=false;}});
    actions.append(recoverButton,deleteButton);actionCell.append(actions);row.append(titleCell,sizeCell,statusCell,actionCell);fragment.append(row);
  });
  $('incompleteRows').replaceChildren(fragment);
  if(pendingRecover&&!sessions.some(item=>item.session===pendingRecover.session))closeRecoverPanel();
  else if(pendingRecover)updateRecoverNote();
}

function openRecoverPanel(item) {
  pendingRecover=item;$('recoverPanel').hidden=false;
  const affordable=sel=>((item.space||{})[`required_${sel}`]||0)<=((state.incomplete||{}).free_bytes||0);
  document.querySelectorAll('input[name="recoverAssets"]').forEach(input=>{input.disabled=!affordable(input.value);if(input.disabled&&input.checked)input.checked=false;});
  if(!document.querySelector('input[name="recoverAssets"]:checked')){const first=[...document.querySelectorAll('input[name="recoverAssets"]')].find(input=>!input.disabled);if(first)first.checked=true;}
  updateRecoverNote();$('recoverPanel').scrollIntoView({block:'nearest'});
}
function closeRecoverPanel() {pendingRecover=null;$('recoverPanel').hidden=true;}
function updateRecoverNote() {
  if(!pendingRecover)return;
  const selected=document.querySelector('input[name="recoverAssets"]:checked'), selection=selected?selected.value:'all';
  const space=(pendingRecover.space||{})[`required_${selection}`]||0, free=(state.incomplete||{}).free_bytes||0;
  const names={all:'全部数据',ir:'仅左右红外',rgb:'仅彩色'};
  $('recoverSpaceNote').textContent=space>free?`可用空间 ${bytes(free)}，少于「${names[selection]}」所需 ${bytes(space)}。可改选范围，或先删除其他数据释放空间。`:`「${names[selection]}」预计需要 ${bytes(space)}，当前可用 ${bytes(free)}。抢救完成后才会删除原始数据。`;
  $('recoverConfirm').disabled=!selected||space>free;
}
$('recoverCancel').addEventListener('click',closeRecoverPanel);
$('recoverConfirm').addEventListener('click',async()=>{
  if(!pendingRecover)return;
  const selected=document.querySelector('input[name="recoverAssets"]:checked');if(!selected)return;
  const item=pendingRecover;pendingRecover=null;$('recoverPanel').hidden=true;
  try{await api('/api/incomplete-recover',{session:item.session,request_id:requestUUID(),assets:selected.value,delete_remainder:$('recoverRemainder').checked});toast('抢救请求已受理：先无损封装为 MP4，校验通过后才删除原始数据。');await poll();}
  catch(error){toast(error.message,true);}
});

async function poll() {if(pollBusy)return;pollBusy=true;try{const previous=state;state=await api('/api/state');for(const task of state.transfers||[]){const old=previous?.transfers?.find(t=>t.recording_id===task.recording_id);if(old&&old.state!=='complete'&&task.state==='complete')toast('下载包已准备好，文件校验通过，点击「下载录制」保存到本机。');}render();await resumePreview();}catch(e){$('connection').classList.remove('online');$('connectionLabel').textContent='设备服务未连接';$('alert').hidden=false;$('alert').textContent='设备网页暂时无响应，请检查设备电源和网络。';$('startButton').disabled=true;$('stopButton').disabled=true;}finally{pollBusy=false;} }

async function capture(action) {
  if(actionBusy||previewBusy)return;
  actionBusy=true;render();
  try {
    if(action==='start') {
      const resume=!!previewId;
      previewId=null;$('previewImage').removeAttribute('src');previewUI(resume?'loading':'idle');
      startRequestId ||= requestUUID();
      const result=await api('/api/start',{request_id:startRequestId,duration:Number($('duration').value)});
      startRequestId=null;
      pendingPreviewJob=resume?result.data.job_id:null;
      toast('启动请求已发送，正在准备采集。');
    } else {
      await api('/api/stop',{job_id:state.status.job_id});
      toast('停止请求已发送，正在保存录制。');
    }
    await poll();
  } catch(error) {pendingPreviewJob=null;previewUI('idle');toast(error.message,true);}
  finally {actionBusy=false;render();}
}

async function resumePreview() {
  if(!pendingPreviewJob||previewBusy||!state.connected)return;
  const capture=state.status||{};
  if(capture.job_id!==pendingPreviewJob)return;
  if(!activeStates.has(capture.state)){pendingPreviewJob=null;previewUI('idle');return;}
  if(!capture.capture_running||!state.preview_source_ready)return;
  pendingPreviewJob=null;
  await togglePreview();
}

function previewUI(mode) {
  const live=mode==='live',loading=mode==='loading';$('viewfinder').classList.toggle('live',live);$('viewfinder').classList.toggle('loading',loading);
  $('previewImage').hidden=!live;$('previewPlaceholder').hidden=live;$('previewCenterButton').hidden=loading;
  $('previewTitle').textContent=loading?'正在连接相机…':'画面尚未开启';$('previewHint').textContent=loading?'首次启动可能需要几秒钟':'开启预览，查看相机当前视野';
  $('previewBadge').textContent=live?'LIVE':loading?'CONNECTING':'STANDBY';$('previewToggle').textContent=previewId?'关闭预览':loading?'连接中…':'开启预览';
  if(!live)$('previewCounter').textContent='RGB / LIVE VIEW';
}
async function togglePreview() {if(previewBusy)return;previewBusy=true;render();try{if(previewId){const sid=previewId;await api('/api/preview/stop',{session_id:sid});previewId=null;$('previewImage').removeAttribute('src');previewUI('idle');}else{previewUI('loading');const result=await api('/api/preview/start',{});previewId=result.data.session_id;previewOpenedAt=Date.now();$('previewImage').src='/api/preview/stream/'+previewId;previewUI('loading');}}catch(error){previewId=null;$('previewImage').removeAttribute('src');previewUI('idle');toast(error.message,true);}finally{previewBusy=false;render();} }
async function renewPreview() {if(!previewId||renewing)return;renewing=true;const sid=previewId;try{await api('/api/preview/renew',{session_id:sid});}catch(error){if(previewId!==sid)return;previewId=null;$('previewImage').removeAttribute('src');previewUI('idle');toast(error.message,true);}finally{renewing=false;} }
$('previewImage').addEventListener('load',()=>{if(previewId)previewUI('live');});
$('previewImage').addEventListener('error',()=>{if(previewId&&!previewBusy){previewUI('loading');const sid=previewId;setTimeout(()=>{if(previewId===sid)$('previewImage').src='/api/preview/stream/'+sid+'?reconnect='+Date.now();},2000);}});
$('startButton').addEventListener('click',()=>capture('start'));$('stopButton').addEventListener('click',()=>capture('stop'));$('previewToggle').addEventListener('click',togglePreview);$('previewCenterButton').addEventListener('click',togglePreview);$('refreshButton').addEventListener('click',poll);
window.addEventListener('pagehide',()=>{if(previewId&&token)fetch('/api/preview/stop',{method:'POST',headers:{'Content-Type':'application/json','X-UMI-Token':token},body:JSON.stringify({session_id:previewId}),keepalive:true}).catch(()=>{});});
setInterval(()=>{if(clockBase)setClock(UMIClock.value(clockBase,performance.now()));},500);
(async()=>{try{const bootstrap=await api('/api/bootstrap');token=bootstrap.token;await poll();setInterval(poll,2000);setInterval(renewPreview,5000);}catch(error){$('alert').hidden=false;$('alert').textContent=error.message;}})();
