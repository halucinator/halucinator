#!/usr/bin/env python3
# Copyright 2026 Christopher Wright

"""Unified web panel for the Bus Pirate v5 demo — terminal + LCD + devices.

One browser tab that shows, live and driven by what you type:
  * an interactive terminal (the colour Bus Pirate console, via xterm.js),
  * the live ST7789 LCD render,
  * a feed of the modeled external devices (SPI flash, EEPROM, sensors, …).

It is an "external device": it bridges to halucinator over ZMQ exactly like
``bpv5_terminal`` (subscribes to the firmware console, publishes keystrokes),
watches the LCD framebuffer PNG, tails halucinator's log for the modeled-device
lines, and serves all of it to the browser over Server-Sent Events. No native
GUI toolkit — it renders in your browser, so there's no Tk to fight.

Two windows:
    python3 -m halucinator.external_devices.bpv5_panel     # start this FIRST
    bash test/firmware-rehosting/bpv5/run_lcd.sh                               # then halucinator

The panel opens http://127.0.0.1:8765 automatically.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from halucinator.external_devices.ioserver import IOServer


# --------------------------------------------------------------------------
# Fan-out hub: peripheral threads publish events; each browser SSE connection
# subscribes to its own queue.
# --------------------------------------------------------------------------
class Hub:
    def __init__(self) -> None:
        self._clients: list = []
        self._lock = threading.Lock()

    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=2000)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self, event: str, data: str) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass


HUB = Hub()
ARGS: argparse.Namespace
IO: IOServer
_first_output = threading.Event()


# --------------------------------------------------------------------------
# ZMQ <-> firmware console
# --------------------------------------------------------------------------
def send_to_firmware(raw: bytes) -> None:
    IO.send_msg("Peripheral.UTTYModel.rx_char_or_buf",
                {"interface_id": ARGS.interface, "char": list(raw)})


def on_tx_buf(server: IOServer, msg: dict) -> None:  # noqa: ARG001
    raw = msg.get("chars", b"")
    if isinstance(raw, list):
        raw = bytes(raw)
    elif not isinstance(raw, (bytes, bytearray)):
        return
    rawb = bytes(raw)
    HUB.publish("tty", base64.b64encode(rawb).decode("ascii"))
    # The firmware probes terminal size with ESC[6n; answer it ourselves
    # (ESC[rows;colsR for the 80x30 xterm). The browser drops xterm's own
    # auto-reply, so the firmware gets exactly one clean response and no stray
    # ESC[..R bytes land in its command line — forwarding those corrupts
    # keystrokes (typing `m` does nothing).
    for _ in range(rawb.count(b"\x1b[6n")):
        send_to_firmware(b"\x1b[30;80R")
    # Send the prelude (default "y\r\n") on the first firmware output — that
    # proves halucinator's SUB is bound, so it isn't lost in the ZMQ
    # slow-joiner window. Mirrors bpv5_terminal.
    if not _first_output.is_set():
        _first_output.set()
        if ARGS.prelude:
            def _send_prelude() -> None:
                time.sleep(ARGS.prelude_delay)
                send_to_firmware(ARGS.prelude.encode("latin-1")
                                 .decode("unicode_escape").encode("latin-1"))
            threading.Thread(target=_send_prelude, daemon=True).start()


# --------------------------------------------------------------------------
# LCD framebuffer watcher -> 'lcd' events (browser re-fetches /lcd.png)
# --------------------------------------------------------------------------
def lcd_watcher() -> None:
    last = -1.0
    while True:
        try:
            mt = os.path.getmtime(ARGS.png)
            if mt != last:
                last = mt
                HUB.publish("lcd", str(mt))
        except OSError:
            pass
        time.sleep(0.2)


# --------------------------------------------------------------------------
# halucinator log tail -> 'dev' events (the modeled-device "[Name] …" lines)
# --------------------------------------------------------------------------
def hal_log_tail() -> None:
    fp = None
    while True:
        if fp is None:
            try:
                fp = open(ARGS.hal_log, "r", errors="replace")
                fp.seek(0, os.SEEK_END)
            except OSError:
                time.sleep(0.5)
                continue
        line = fp.readline()
        if not line:
            time.sleep(0.15)
            continue
        line = line.rstrip("\n")
        if line.startswith("["):           # modeled-device prints
            HUB.publish("dev", line)


# --------------------------------------------------------------------------
# HTTP / SSE server
# --------------------------------------------------------------------------
INDEX_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Bus Pirate v5 — live panel</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css"/>
<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
<style>
  html,body{margin:0;height:100%;background:#0a0a0a;color:#cdd;font-family:Menlo,monospace}
  #wrap{display:flex;height:100vh;gap:10px;padding:10px;box-sizing:border-box}
  #left{flex:1 1 60%;display:flex;flex-direction:column;min-width:0}
  #right{flex:0 0 360px;display:flex;flex-direction:column;gap:10px}
  .card{background:#141414;border:1px solid #262626;border-radius:8px;padding:8px}
  .h{color:#8ab;font-size:12px;margin:0 0 6px 2px;letter-spacing:.04em}
  #term{flex:1;min-height:0}
  #lcd{image-rendering:pixelated;width:320px;height:240px;background:#000;border:1px solid #333;display:block}
  #lcdwrap{align-self:center}
  #dev{height:240px;overflow:auto;font-size:11px;line-height:1.35;white-space:pre-wrap;color:#9c9}
  #dev .spi{color:#fc7}#dev .stor{color:#9bd}
  .dot{color:#6c6}
  #tiles{display:flex;gap:8px;margin-bottom:6px}
  .tile{background:#0f0f0f;border:1px solid #242424;border-radius:6px;padding:6px;text-align:center}
  .tlbl{color:#7a8;font-size:10px;letter-spacing:.05em;margin-bottom:4px}
  .swatch{width:40px;height:40px;border-radius:6px;border:1px solid #333;margin:0 auto;background:#000}
  .tval{font-size:11px;color:#cdd;margin-top:4px}
  #volts{text-align:left}
  .vmain{font-size:12px;margin-bottom:3px}.vmain b{color:#7df}
  .vb{display:flex;align-items:center;gap:5px;font-size:10px;margin:1px 0}
  .vbl{width:26px;color:#9ab}
  .vbar{flex:1;height:7px;background:#1c1c1c;border-radius:3px;overflow:hidden}
  .vbar i{display:block;height:100%;background:#3a7}
  .vbv{width:34px;text-align:right;color:#cdd}
  #active{font-size:11px;color:#bd9;background:#0f0f0f;border:1px solid #242424;border-radius:6px;padding:5px}
  #active b{color:#fd8}
  #ledstrip{display:flex;flex-wrap:wrap;gap:5px;padding:4px 0;min-height:22px}
  .ledpix{width:16px;height:16px;border-radius:50%;border:1px solid #2a2a2a;background:#0a0a0a}
  .ledpix.on{border-color:#666}
  #ledstat{font-size:11px;color:#9ab;margin-top:3px}
  #ledctl{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:6px;font-size:11px;color:#9ab}
  #ledctl input[type=color]{width:28px;height:22px;padding:0;border:1px solid #333;background:#0a0a0a;cursor:pointer}
  #ledctl select,#ledctl button{font-size:11px;background:#1c1c1c;color:#9ab;border:1px solid #333;border-radius:4px;padding:2px 7px;cursor:pointer}
  #ledctl button:hover{border-color:#666;color:#cdd}
  #ledctl .preset{width:18px;height:18px;border-radius:50%;border:1px solid #555;cursor:pointer;padding:0}
  #ledpixrow{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px;width:100%}
  #ledpixrow .pixin{width:24px;height:20px;padding:0;border:1px solid #333;background:#0a0a0a;cursor:pointer}
  #disp{background:#000;border:1px solid #2a2a2a;border-radius:6px;padding:8px}
  .drow{display:flex;gap:8px;align-items:center;font-size:12px;margin-bottom:6px}
  .dk{color:#7a8;font-size:10px}
  .dv{color:#7df;font-weight:bold;margin-right:8px}
  .dpins{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}
  .dpin{background:#0e0e0e;border:1px solid #222;border-radius:4px;padding:3px 5px;font-size:10px;color:#9ab}
  .dpin .pv{color:#fd8;float:right}
  .dbar{height:4px;background:#1c1c1c;border-radius:2px;margin-top:3px;overflow:hidden}
  .dbar i{display:block;height:100%;background:#3a7}
  #d_ctrl{margin-top:8px;border-top:1px solid #222;padding-top:6px}
  .ctlhdr{color:#7a8;font-size:10px;margin-bottom:4px}
  .ctlhdr button{margin-left:8px;font-size:10px;background:#1c1c1c;color:#9ab;border:1px solid #333;border-radius:4px;padding:1px 6px;cursor:pointer}
  .ctlhdr button:hover{border-color:#666;color:#cdd}
  .ctl{display:flex;align-items:center;gap:6px;margin:1px 0}
  .ctl .cl{width:28px;color:#9ab;font-size:10px}
  .ctl input[type=range]{flex:1;height:4px;accent-color:#3a7}
  .ctl .cv{width:46px;text-align:right;color:#fd8;font-size:10px}
</style></head>
<body><div id="wrap">
  <div id="left" class="card">
    <p class="h">TERMINAL — type Bus Pirate commands (m, d 2, [0x9f r:3] …)</p>
    <div id="term"></div>
  </div>
  <div id="right">
    <div class="card">
      <p class="h">DEVICE DISPLAY — live (synthesized from device state)</p>
      <div id="disp">
        <div class="drow">
          <span class="dk">VOUT</span><span id="d_vout" class="dv">--</span>
          <span class="dk">VREF</span><span id="d_vref" class="dv">--</span>
          <span class="dk">MODE</span><span id="d_mode" class="dv">HiZ</span>
        </div>
        <div id="d_pins" class="dpins"></div>
        <div id="d_ctrl"></div>
      </div>
    </div>
    <div class="card" id="lcdwrap">
      <p class="h">LCD — boot capture (pixel-faithful ST7789) <span id="lcdstat" class="dot">●</span></p>
      <img id="lcd" alt="waiting for LCD …"/>
    </div>
    <div class="card">
      <p class="h">LED STRIP — WS2812 / APA102 (live)</p>
      <div id="ledstrip"></div>
      <div id="ledstat">no frames yet</div>
      <div id="ledctl">
        <select id="ledcount">
          <option>1</option><option>2</option><option>3</option><option>4</option>
          <option>5</option><option>6</option><option>7</option><option selected>8</option>
        </select><span>LEDs</span>
        <button id="ledset">Set strip</button>
        <button id="ledoff">All off</button>
        <span style="margin-left:4px">fill:</span><span id="ledpresets"></span>
        <div id="ledpixrow"></div>
      </div>
    </div>
    <div class="card">
      <p class="h">ACTIVE DEVICE</p>
      <div id="active">active: <b>—</b></div>
    </div>
    <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0">
      <p class="h">DEVICE FEED — modeled-target I/O</p>
      <div id="dev"></div>
    </div>
  </div>
</div>
<script>
const term = new Terminal({cols:80, rows:30, convertEol:false, fontSize:13,
  theme:{background:'#0a0a0a'}});
term.open(document.getElementById('term'));
term.focus();
// keystrokes -> firmware (UTF-8 -> base64) + local echo
const LOCAL_ECHO = true;
term.onData(d => {
  // Drop the emulator's auto cursor-position reply (ESC[..R): the panel
  // answers the firmware's ESC[6n itself, so forwarding xterm's reply too
  // would inject ESC[..R into the firmware's command line and corrupt keys.
  if(/^\x1b\[[0-9;]*R$/.test(d)) return;
  const b = new TextEncoder().encode(d);
  let s=''; b.forEach(x=>s+=String.fromCharCode(x));
  fetch('/input',{method:'POST',body:btoa(s)});
  // The firmware reads injected input via the rx-fifo bridge and does NOT
  // echo it (a real unit echoes from its USB read loop, which we bypass), so
  // nothing would appear as you type. Echo printable keys + Enter/Backspace
  // ourselves. Skip escape sequences (d starts with ESC) so the terminal's
  // cursor-position replies and arrow keys don't print as junk.
  if(LOCAL_ECHO && d.charCodeAt(0)!==0x1b){
    let out='';
    for(const ch of d){
      const c=ch.charCodeAt(0);
      if(c===0x0d||c===0x0a) out+='\r\n';
      else if(c===0x7f||c===0x08) out+='\b \b';
      else if(c>=0x20) out+=ch;
    }
    if(out) term.write(out);
  }
});
const dev = document.getElementById('dev');
const lcd = document.getElementById('lcd');
const ledstrip=document.getElementById('ledstrip'), ledstat=document.getElementById('ledstat'),
      active=document.getElementById('active');
const d_vout=document.getElementById('d_vout'), d_vref=document.getElementById('d_vref'),
      d_mode=document.getElementById('d_mode'), d_pins=document.getElementById('d_pins'),
      d_ctrl=document.getElementById('d_ctrl');
const IO_DEFAULTS=[400,800,1200,1600,2000,2400,2800,3200];
const ioVals=IO_DEFAULTS.slice();
const ioSliders=[], ioSpans=[];
function resetIO(){
  IO_DEFAULTS.forEach((mv,i)=>syncIO(i,mv));
  renderPins(ioVals);
  fetch('/adc',{method:'POST',body:JSON.stringify({reset:true})});  // model -> config levels
}
function renderPins(vals){
  let h='';
  vals.forEach((mv,i)=>{ const pct=Math.min(100,mv/3300*100);
    h+='<div class="dpin"><span class="pv">'+(mv/1000).toFixed(2)+'V</span>IO'+i+
       '<div class="dbar"><i style="width:'+pct+'%"></i></div></div>'; });
  d_pins.innerHTML=h;
}
let _adcT=null;
function postAdc(){ clearTimeout(_adcT); _adcT=setTimeout(()=>{
  fetch('/adc',{method:'POST',body:JSON.stringify({pin_mv:ioVals})}); }, 150); }
function syncIO(i,mv){ if(i<0||i>7) return; ioVals[i]=mv;
  if(ioSliders[i]){ ioSliders[i].value=mv; ioSpans[i].textContent=(mv/1000).toFixed(2)+'V'; } }
(function(){
  d_ctrl.innerHTML='<div class="ctlhdr">SET IO INPUTS — drag to change (live) '
    +'<button id="ioreset">reset to config</button></div>';
  for(let i=0;i<8;i++){
    const row=document.createElement('div'); row.className='ctl';
    const lbl=document.createElement('span'); lbl.className='cl'; lbl.textContent='IO'+i;
    const s=document.createElement('input'); s.type='range'; s.min='0'; s.max='3300'; s.step='10'; s.value=ioVals[i];
    const v=document.createElement('span'); v.className='cv'; v.textContent=(ioVals[i]/1000).toFixed(2)+'V';
    s.addEventListener('input',()=>{ ioVals[i]=+s.value; v.textContent=(ioVals[i]/1000).toFixed(2)+'V';
      renderPins(ioVals); postAdc(); });
    row.appendChild(lbl); row.appendChild(s); row.appendChild(v);
    d_ctrl.appendChild(row); ioSliders[i]=s; ioSpans[i]=v;
  }
  document.getElementById('ioreset').addEventListener('click', resetIO);
  renderPins(ioVals);   // show the table immediately (no need to wait for `v`)
})();
// --- LED quick-set: inject the CLI commands to drive the strip -------------
function sendKeys(s){
  const b=new TextEncoder().encode(s); let bin=''; b.forEach(x=>bin+=String.fromCharCode(x));
  fetch('/input',{method:'POST',body:btoa(bin)});
}
function ledWord(r,g,b){ const w=(((g<<16)|(r<<8)|b)>>>0);  // WS2812 word = G:R:B
  return '0x'+('000000'+w.toString(16)).slice(-6).toUpperCase(); }
function setLeds(words){
  const frame='['+words.join(' ')+']';
  const pre=(d_mode.textContent==='LED')?'':'m\r10\r1\r';   // enter LED mode if needed
  sendKeys(pre+frame+'\r');
}
function hslHex(h){  // h in [0,360), full sat/half light -> a distinct hue
  const x=1-Math.abs((h/60)%2-1); let r,g,b;
  if(h<60){r=1;g=x;b=0}else if(h<120){r=x;g=1;b=0}else if(h<180){r=0;g=1;b=x}
  else if(h<240){r=0;g=x;b=1}else if(h<300){r=x;g=0;b=1}else{r=1;g=0;b=x}
  const f=v=>('0'+Math.round(v*255).toString(16)).slice(-2); return '#'+f(r)+f(g)+f(b);
}
function hexWord(hex){ return ledWord(parseInt(hex.substr(1,2),16),parseInt(hex.substr(3,2),16),parseInt(hex.substr(5,2),16)); }
(function(){
  const cnt=document.getElementById('ledcount'), row=document.getElementById('ledpixrow'), pix=[];
  function rebuild(){
    const N=+cnt.value, old=pix.map(p=>p.value);
    row.innerHTML=''; pix.length=0;
    for(let i=0;i<N;i++){
      const inp=document.createElement('input'); inp.type='color'; inp.className='pixin';
      inp.value=old[i]||hslHex(i*360/N); inp.title='LED '+i;   // rainbow default = obviously per-LED
      row.appendChild(inp); pix.push(inp);
    }
  }
  cnt.onchange=rebuild;
  document.getElementById('ledset').onclick=()=> setLeds(pix.map(p=>hexWord(p.value)));
  document.getElementById('ledoff').onclick=()=>{ pix.forEach(p=>p.value='#000000'); setLeds(pix.map(()=>'0x000000')); };
  const box=document.getElementById('ledpresets');
  [['Red',255,0,0],['Green',0,255,0],['Blue',0,0,255],['White',255,255,255]].forEach(([name,r,g,b])=>{
    const btn=document.createElement('button'); btn.className='preset'; btn.title='fill '+name;
    btn.style.background='rgb('+r+','+g+','+b+')';
    const hex='#'+[r,g,b].map(x=>('0'+x.toString(16)).slice(-2)).join('');
    btn.onclick=()=>{ pix.forEach(p=>p.value=hex); setLeds(pix.map(()=>ledWord(r,g,b))); };
    box.appendChild(btn);
  });
  rebuild();
})();
const NAMES={SpiFlashTarget:'SPI flash',I2cEepromTarget:'I²C EEPROM',Ds18b20Target:'1-Wire DS18B20',
  JtagTarget:'JTAG',UartPeerTarget:'UART peer',DioPinTarget:'DIO pins',TwoWireTarget:'2-Wire',
  ThreeWireTarget:'3-Wire',InfraredNecTarget:'IR (NEC)',LedStripSink:'LED strip',ScopeModel:'Scope',
  AdcPsuModel:'ADC / PSU',NandStorage:'SPI-NAND',Storage:'SPI-NAND'};
function setActive(name,line){ active.innerHTML='active: <b>'+name+'</b> &mdash; '+
  line.replace(/^\[[^\]]+\]\s*/,'').slice(0,72); }
function hx(n){return ('0'+(n&255).toString(16)).slice(-2).toUpperCase();}
let ledFrame=[];
function renderStrip(leds){
  ledstrip.innerHTML='';
  leds.forEach(p=>{
    const on=(p.r||p.g||p.b);
    const d=document.createElement('span');
    d.className='ledpix'+(on?' on':'');
    if(on){ d.style.background='rgb('+p.r+','+p.g+','+p.b+')';
            d.style.boxShadow='0 0 6px rgb('+p.r+','+p.g+','+p.b+')'; }
    d.title='#'+hx(p.r)+hx(p.g)+hx(p.b);
    ledstrip.appendChild(d);
  });
  const lit=leds.filter(p=>p.r||p.g||p.b).length;
  ledstat.textContent = leds.length? (leds.length+' LED(s), '+lit+' on — '+
    leds.map(p=>'#'+hx(p.r)+hx(p.g)+hx(p.b)).join(' ')) : 'no frames yet';
}
function parseDev(line){
  if(line.indexOf('FRAME START')>=0){ ledFrame=[]; renderStrip(ledFrame); setActive('LED strip',line); return; }
  let m=line.match(/decoded R=0x([0-9a-fA-F]{2}) G=0x([0-9a-fA-F]{2}) B=0x([0-9a-fA-F]{2})/);
  if(m){ ledFrame.push({r:parseInt(m[1],16),g:parseInt(m[2],16),b:parseInt(m[3],16)});
    renderStrip(ledFrame); setActive('LED strip',line); return; }
  if(line.indexOf('FRAME END')>=0){ renderStrip(ledFrame); return; }
  m=line.match(/APA102 FRAME word=0x[0-9a-fA-F]{2}([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})/);
  if(m){ ledFrame.push({r:parseInt(m[1],16),g:parseInt(m[2],16),b:parseInt(m[3],16)});
    renderStrip(ledFrame); setActive('LED strip',line); return; }
  m=line.match(/VOUT=(\d+) mV VREF=(\d+) mV;\s*(IO0=[^()]*)/);
  if(m){
    d_vout.textContent=(m[1]/1000).toFixed(2)+'V';
    d_vref.textContent=(m[2]/1000).toFixed(2)+'V';
    m[3].trim().split(/\s+/).forEach(p=>{ const mm=p.match(/IO(\d)=(\d+)/);
      if(mm) syncIO(+mm[1], +mm[2]); });   // reflect the firmware's reading in the sliders
    renderPins(ioVals); setActive('ADC / PSU',line); return; }
  m=line.match(/psu_enable\(set=[\d.]+ V -> (\d+) mV\)/);   // W sets the supply
  if(m){ d_vout.textContent=(m[1]/1000).toFixed(2)+'V'; setActive('ADC / PSU',line); return; }
  if(line.indexOf('psu_disable -> OFF')>=0){ d_vout.textContent='3.30V'; setActive('ADC / PSU',line); return; }
  m=line.match(/^\[(\w+)\]/);
  if(m && NAMES[m[1]] && line.indexOf('attached')<0) setActive(NAMES[m[1]],line);
}
const es = new EventSource('/events');
let ttytail='';
es.addEventListener('tty', e => {
  const bin = atob(e.data); const a = new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) a[i]=bin.charCodeAt(i);
  term.write(a);
  // sniff the firmware's mode prompt (e.g. "SPI>") for the synthesized display
  ttytail = (ttytail + bin).slice(-300);
  const pm = ttytail.replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g,'')
                    .match(/(HiZ|1WIRE|HDUART|UART|I2C|SPI|2WIRE|3WIRE|DIO|LED|INFRARED|JTAG)>/g);
  if(pm) d_mode.textContent = pm[pm.length-1].replace('>','');
});
es.addEventListener('lcd', e => { lcd.src = '/lcd.png?t=' + e.data; });
es.addEventListener('dev', e => {
  const line = e.data;
  const d = document.createElement('div');
  if(line.indexOf('SpiFlash')>=0||line.indexOf('MOSI')>=0) d.className='spi';
  else if(line.indexOf('[Storage]')>=0) d.className='stor';
  d.textContent = line;
  dev.appendChild(d);
  while(dev.childNodes.length>400) dev.removeChild(dev.firstChild);
  dev.scrollTop = dev.scrollHeight;
  parseDev(line);
});
es.onerror = () => { document.getElementById('lcdstat').style.color='#a55'; };
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:  # noqa: ARG002 — quiet
        pass

    def _send(self, code: int, ctype: str, body: bytes, cache: bool = True) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            # no-store so a browser reload always gets the latest panel UI
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode(), cache=False)
        elif path == "/lcd.png":
            try:
                with open(ARGS.png, "rb") as fp:
                    self._send(200, "image/png", fp.read(), cache=False)
            except OSError:
                self._send(404, "text/plain", b"no lcd yet")
        elif path == "/events":
            self._sse()
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/input":
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n) if n else b""
            try:
                send_to_firmware(base64.b64decode(body))
            except Exception:  # noqa: BLE001
                pass
            self._send(200, "text/plain", b"ok")
        elif self.path == "/adc":
            # Live IO/rail overrides → JSON file the AdcPsuModel re-reads each
            # sweep (no restart). Body: {"pin_mv":[8],"vout_mv":..,"vref_mv":..}
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n) if n else b"{}"
            try:
                data = json.loads(body)
                if data.get("reset"):
                    try:               # revert the model to its config levels
                        os.remove(ARGS.adc_file)
                    except OSError:
                        pass
                else:
                    with open(ARGS.adc_file, "w") as fh:
                        json.dump(data, fh)
            except Exception:  # noqa: BLE001
                pass
            self._send(200, "text/plain", b"ok")
        else:
            self._send(404, "text/plain", b"not found")

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = HUB.subscribe()
        try:
            self._evt("lcd", str(time.time()))   # nudge an initial LCD fetch
            while True:
                try:
                    event, data = q.get(timeout=15)
                    self._evt(event, data)
                except queue.Empty:
                    self._evt("ping", "")        # keep the connection alive
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            HUB.unsubscribe(q)

    def _evt(self, event: str, data: str) -> None:
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()


def main(argv=None) -> int:
    global ARGS, IO
    p = argparse.ArgumentParser(description="Unified web panel for bpv5")
    p.add_argument("-r", "--rx-port", type=int, default=5556,
                   help="ZMQ port to SUB on (firmware tx)")
    p.add_argument("-t", "--tx-port", type=int, default=5555,
                   help="ZMQ port to PUB on (firmware rx)")
    p.add_argument("--interface", default="BP5")
    p.add_argument("--http-port", type=int, default=8765)
    p.add_argument("--png", default=os.environ.get("BPV5_LCD_PNG", "/tmp/bpv5_lcd.png"))
    p.add_argument("--hal-log", default=os.environ.get("BPV5_HAL_LOG", "/tmp/bpv5_hal.log"))
    p.add_argument("--adc-file", default=os.environ.get("BPV5_ADC_FILE", "/tmp/bpv5_adc.json"),
                   help="JSON file of live IO/rail overrides the AdcPsuModel re-reads")
    p.add_argument("--prelude", default="y\\r\\n",
                   help="bytes to send on first firmware output ('' to disable)")
    p.add_argument("--prelude-delay", type=float, default=0.3)
    p.add_argument("--no-open", action="store_true", help="don't open a browser")
    ARGS = p.parse_args(argv)

    IO = IOServer(rx_port=ARGS.rx_port, tx_port=ARGS.tx_port)
    IO.register_topic("Peripheral.UTTYModel.tx_buf", on_tx_buf)
    IO.start()

    threading.Thread(target=lcd_watcher, daemon=True).start()
    threading.Thread(target=hal_log_tail, daemon=True).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", ARGS.http_port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{ARGS.http_port}"
    sys.stderr.write(
        f"[bpv5_panel] subscribed to the firmware console (rx={ARGS.rx_port} "
        f"tx={ARGS.tx_port})\n"
        f"[bpv5_panel] LCD: {ARGS.png}   device log: {ARGS.hal_log}\n"
        f"[bpv5_panel] open {url}  (start halucinator with: bash test/firmware-rehosting/bpv5/run_lcd.sh)\n")
    sys.stderr.flush()
    if not ARGS.no_open:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[bpv5_panel] shutting down\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
