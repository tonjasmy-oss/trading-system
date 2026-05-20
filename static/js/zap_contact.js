(function(){
var d=false;
function z(){
if(d)return;
var x=document.evaluate('//text()[normalize-space()="联系我们"]',document.body,null,XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,null);
for(var i=0;i<x.snapshotLength;i++){
var t=x.snapshotItem(i),e=t.parentElement;
if(!e||e.closest('#global-footer'))continue;
var p=e;
while(p&&p!==document.body){
var tg=(p.tagName||''),cl=(p.className||'').toString();
if(tg==='LI'||cl.indexOf('menu-item')>=0)break;
if(tg==='ASIDE'||cl.indexOf('sider')>=0){p=e;break}
p=p.parentElement
}
if(p&&p!==document.body){
var s=document.createElement('style');
s.id='zap-contact';
s.textContent='aside [class*="footer"],.ant-layout-sider [class*="footer"]{display:none!important}';
if(!document.getElementById('zap-contact'))document.head.appendChild(s);
if(p.parentNode)p.parentNode.removeChild(p);
d=true;return
}
}
}
setInterval(z,800)
})();
