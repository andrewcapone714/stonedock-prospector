// Pebble, the StoneDock Prospector's AI helper: the blue middle stone of the StoneDock logo.
// Pebble.mount(el) draws it; Pebble.set(state) switches what it's doing:
//   hello · idle · think · search · type · read · done · error   (work)
//   run · lift · water · meditate                                (idle easter eggs: 8 s idle, then a routine, repeat)
// Multi-step scenes (drinking, page turning, hello) run on one JavaScript timeline so their parts can't drift apart.
(function () {
  const CSS = `
#pbSvg .pb-x{display:none}
#pbSvg.s-idle .f-normal,#pbSvg.s-run .f-normal,#pbSvg.s-lift .f-strain,#pbSvg.s-think .f-think,#pbSvg.s-search .f-search,#pbSvg.s-type .f-down,#pbSvg.s-read .f-read,#pbSvg.s-done .f-happy,#pbSvg.s-error .f-shock,#pbSvg.s-meditate .f-calm,#pbSvg.s-hello .f-hello,#pbSvg.s-hello2 .f-hello{display:inline}
#pbSvg.s-idle .a-down,#pbSvg.s-hello2 .a-down,#pbSvg.s-think .a-think,#pbSvg.s-search .a-search,#pbSvg.s-type .a-type,#pbSvg.s-read .a-read,#pbSvg.s-done .a-up,#pbSvg.s-error .a-shock,#pbSvg.s-run .a-run,#pbSvg.s-lift .a-lift,#pbSvg.s-meditate .a-lap,#pbSvg.s-water .a-water,#pbSvg.s-hello .a-hello{display:inline}
#pbSvg.s-think .p-think,#pbSvg.s-type .p-type,#pbSvg.s-read .p-read,#pbSvg.s-done .p-done,#pbSvg.s-error .p-error,#pbSvg.s-run .p-run,#pbSvg.s-meditate .p-meditate,#pbSvg.s-hello .p-hello,#pbSvg.s-hello2 .p-hello2{display:inline}
#pbSvg.s-dyn .a-dyn,#pbSvg.s-dyn .pb-props{display:inline}
#pbSvg.s-dyn.fx-normal .f-normal,#pbSvg.s-dyn.fx-strain .f-strain,#pbSvg.s-dyn.fx-happy .f-happy,#pbSvg.s-dyn.fx-calm .f-calm{display:inline}
#pbSvg.s-run .p-band,#pbSvg.hb .p-band{display:inline}
#pbSvg.s-run .p-sweatrun,#pbSvg.s-lift .p-sweatrun,#pbSvg.sweaty .p-sweatrun{display:inline}
/* sweat builds up gradually: full after 3 s of jogging, and by the 5th rep of lifting (5 × 1.2 s) */
#pbSvg.s-run .p-sweatrun{animation:pb-sweatin 3s linear both}
#pbSvg.s-lift .p-sweatrun{animation:pb-sweatin 6s linear both}
@keyframes pb-sweatin{from{opacity:0}to{opacity:1}}
#pbSvg.s-dyn.fx-wink .f-wink{display:inline}
#pbSvg.flex .a-flex{display:inline}#pbSvg.flex #pb-dR,#pbSvg.flex #pb-dHR{display:none}
#pbSvg.cross .pb-legs{display:none}#pbSvg.cross .p-cross{display:inline}
#pbSvg.breathe #pb-bd{animation:pb-bob 5s ease-in-out infinite}
#pbSvg .pb-pump{transform-box:fill-box;transform-origin:center;animation:pb-pump .45s ease-in-out infinite alternate}
@keyframes pb-pump{to{transform:scale(1.25)}}
#pbSvg.backR .a-dynback{display:inline}#pbSvg.backR #pb-dR,#pbSvg.backR #pb-dHR{display:none}
#pbSvg .pb-legs{display:inline}#pbSvg.s-meditate .pb-legs{display:none}#pbSvg.s-meditate .p-cross{display:inline}
#pbSvg #pb-bd{transform-box:view-box;transform-origin:150px 205px}
#pbSvg.s-idle #pb-bd,#pbSvg.s-read #pb-bd,#pbSvg.s-think #pb-bd,#pbSvg.s-hello2 #pb-bd,#pbSvg.s-search #pb-bd{animation:pb-bob 3s ease-in-out infinite}
#pbSvg.s-meditate #pb-bd{animation:pb-bob 5s ease-in-out infinite}
#pbSvg.s-run #pb-bd{animation:pb-bob .35s ease-in-out infinite}
#pbSvg.s-done #pb-bd{animation:pb-hop .6s ease-out 3}
#pbSvg.s-error #pb-bd{animation:pb-jolt .4s ease-out 1}
#pbSvg.s-hello #pb-bd{animation:pb-hop .5s ease-out 1}
#pbSvg .p-hello2{transform-box:fill-box;transform-origin:20% 100%;animation:pb-pop .3s ease-out}
#pbSvg .pb-blink{transform-box:fill-box;transform-origin:center;animation:pb-blink 4s infinite}
#pbSvg.s-search .pb-lpup{animation:pb-lk 1.8s ease-in-out infinite}
#pbSvg .p-think circle{animation:pb-dot 1.2s infinite}#pbSvg .p-think circle:nth-child(2){animation-delay:.2s}#pbSvg .p-think circle:nth-child(3){animation-delay:.4s}
#pbSvg .pb-tyL{animation:pb-tyL .2s ease-in-out infinite alternate}#pbSvg .pb-tyR{animation:pb-tyR .2s ease-in-out infinite alternate-reverse}
#pbSvg .pb-hL{animation:pb-hL .2s ease-in-out infinite alternate;transform-box:view-box}#pbSvg .pb-hR{animation:pb-hR .2s ease-in-out infinite alternate-reverse;transform-box:view-box}
#pbSvg .pb-swL{transform-box:view-box;transform-origin:96px 150px;animation:pb-sw .35s infinite alternate}
#pbSvg .pb-swR{transform-box:view-box;transform-origin:204px 150px;animation:pb-sw .35s infinite alternate-reverse}
#pbSvg .pb-lgL{transform-box:view-box;transform-origin:134px 182px;animation:pb-sw .35s infinite alternate-reverse;animation-play-state:paused}
#pbSvg .pb-lgR{transform-box:view-box;transform-origin:166px 182px;animation:pb-sw .35s infinite alternate;animation-play-state:paused}
#pbSvg.s-run .pb-lgL,#pbSvg.s-run .pb-lgR{animation-play-state:running}
#pbSvg .pb-wind{animation:pb-wind .7s linear infinite}
#pbSvg .pb-bar{animation:pb-bar 1.2s ease-in-out infinite;transform-box:view-box}
#pbSvg .pb-armLL{animation:pb-aL 1.2s ease-in-out infinite}#pbSvg .pb-armLR{animation:pb-aR 1.2s ease-in-out infinite}
#pbSvg .pb-wave{transform-box:view-box;transform-origin:204px 148px;animation:pb-wave .45s ease-in-out 4 alternate}
#pbSvg .pb-drop{animation:pb-drop 1.4s ease-in infinite}
#pbSvg .pb-drip{animation:pb-drop 1s ease-in infinite}
#pbSvg .pb-spark{transform-box:fill-box;transform-origin:center;animation:pb-spk .9s ease-out infinite}
@keyframes pb-bob{50%{transform:translateY(-4px)}}
@keyframes pb-hop{40%{transform:translateY(-20px)}}
@keyframes pb-jolt{30%{transform:translateY(-8px) scale(1.04)}}
@keyframes pb-pop{from{transform:scale(.6);opacity:0}to{transform:scale(1);opacity:1}}
@keyframes pb-blink{0%,94%,100%{transform:scaleY(1)}97%{transform:scaleY(.1)}}
@keyframes pb-lk{0%,100%{transform:translate(-3px,-1px)}50%{transform:translate(3px,1px)}}
@keyframes pb-dot{0%,100%{opacity:.2}50%{opacity:1}}
@keyframes pb-tyL{from{d:path("M96 152 Q98 170 122 172")}to{d:path("M96 152 Q100 164 124 164")}}
@keyframes pb-tyR{from{d:path("M204 152 Q202 170 178 172")}to{d:path("M204 152 Q200 164 176 164")}}
@keyframes pb-hL{from{transform:translate(0,0)}to{transform:translate(2px,-7px)}}
@keyframes pb-hR{from{transform:translate(0,0)}to{transform:translate(-2px,-7px)}}
@keyframes pb-sw{from{transform:rotate(-28deg)}to{transform:rotate(28deg)}}
@keyframes pb-wind{from{transform:translateX(0);opacity:1}to{transform:translateX(-26px);opacity:0}}
@keyframes pb-bar{0%,100%{transform:translateY(0)}50%{transform:translateY(-16px)}}
@keyframes pb-aL{0%,100%{d:path("M96 152 Q86 128 112 106")}50%{d:path("M96 152 Q84 118 112 90")}}
@keyframes pb-aR{0%,100%{d:path("M204 152 Q214 128 188 106")}50%{d:path("M204 152 Q216 118 188 90")}}
@keyframes pb-wave{from{transform:rotate(-12deg)}to{transform:rotate(14deg)}}
@keyframes pb-drop{from{transform:translateY(0);opacity:1}to{transform:translateY(16px);opacity:0}}
@keyframes pb-spk{0%{transform:scale(.3);opacity:0}50%{opacity:1}100%{transform:scale(1.2);opacity:0}}`;

  const HAND = 'href="#pb-hand"';
  const SVG = `<svg id="pbSvg" class="s-idle" viewBox="0 0 300 230" role="img" aria-label="Pebble, the AI helper" style="width:100%;height:auto;display:block">
<defs>
<g id="pb-hand"><circle r="6" fill="#4f7fd9" stroke="#2f5fbe" stroke-width="2"/><g fill="#4f7fd9" stroke="#2f5fbe" stroke-width="1.5"><ellipse cx="-4" cy="-6" rx="2.4" ry="3.4"/><ellipse cx="0" cy="-7.5" rx="2.4" ry="3.4"/><ellipse cx="4" cy="-6" rx="2.4" ry="3.4"/><ellipse cx="6.5" cy="0" rx="3.2" ry="2.3"/></g></g>
<g id="pb-lines" stroke="#888780" stroke-width="1.3"><path d="M4 7l28-3.9M4 12l28-3.9M4 17l28-3.9M4 22l24-3.3M4 27l28-3.9"/></g>
</defs>
<ellipse cx="150" cy="208" rx="62" ry="6" fill="#888780" opacity=".25"/>
<g id="pb-p-base" display="none"><ellipse rx="68" ry="17" fill="#4b5563"/><path d="M-40 -4q14-6 30-2M12 6q16 3 30-3" stroke="#374151" stroke-width="2" fill="none" stroke-linecap="round"/></g>
<g class="pb-x p-run"><g class="pb-wind" stroke="#85B7EB" stroke-width="3" stroke-linecap="round"><line x1="66" y1="140" x2="82" y2="140"/><line x1="60" y1="158" x2="78" y2="158"/></g></g>
<g class="pb-x p-think" fill="#378ADD"><circle cx="210" cy="104" r="5"/><circle cx="226" cy="92" r="6"/><circle cx="244" cy="78" r="7"/></g>
<g class="pb-x p-done" fill="#EF9F27"><path class="pb-spark" d="M70 96l4 10 10 4-10 4-4 10-4-10-10-4 10-4z"/><path class="pb-spark" d="M234 90l3 8 8 3-8 3-3 8-3-8-8-3 8-3z" style="animation-delay:.3s"/></g>
<g class="pb-x p-hello"><rect x="190" y="40" width="100" height="34" rx="10" fill="#E6F1FB" stroke="#185FA5"/><path d="M208 74l-6 12 16-12z" fill="#E6F1FB" stroke="#185FA5"/><text x="240" y="62" text-anchor="middle" font-size="14" fill="#0C447C" font-weight="600">Hi, I'm Pebble!</text></g>
<g class="pb-x p-hello2"><rect x="148" y="22" width="148" height="54" rx="12" fill="#E6F1FB" stroke="#185FA5"/><path d="M172 76l-8 14 20-14z" fill="#E6F1FB" stroke="#185FA5"/><text x="222" y="45" text-anchor="middle" font-size="13" fill="#0C447C" font-weight="600">Your Personal AI</text><text x="222" y="63" text-anchor="middle" font-size="13" fill="#0C447C" font-weight="600">Helper and Researcher!</text></g>
<text id="pb-ahh" x="232" y="96" text-anchor="middle" font-size="16" fill="#185FA5" font-weight="600" display="none">Ahh</text>
<text id="pb-glug" x="92" y="112" text-anchor="middle" font-size="13" fill="#185FA5" display="none">glug</text>
<g id="pb-lift"><g id="pb-bd">
<g class="pb-legs" stroke="#2f5fbe" stroke-width="6" stroke-linecap="round" fill="#2f5fbe"><g class="pb-lgL"><line x1="134" y1="182" x2="132" y2="200"/><ellipse cx="127" cy="202" rx="9" ry="4.5" stroke="none"/></g><g class="pb-lgR"><line x1="166" y1="182" x2="168" y2="200"/><ellipse cx="173" cy="202" rx="9" ry="4.5" stroke="none"/></g></g>
<g class="pb-x p-cross" fill="#2f5fbe"><ellipse cx="136" cy="190" rx="22" ry="7"/><ellipse cx="164" cy="190" rx="22" ry="7"/></g>
<g class="pb-x a-dynback" fill="none" stroke="#2f5fbe" stroke-width="6" stroke-linecap="round"><path id="pb-dRb" d="M204 152 Q216 140 220 124"/><use id="pb-dHRb" ${HAND}/></g>
<ellipse cx="150" cy="150" rx="58" ry="38" fill="#4f7fd9"/>
<path d="M110 138q12-8 26-2M162 172q14 4 26-4M118 168q8 4 16 2" stroke="#2f5fbe" stroke-width="2" fill="none" stroke-linecap="round" opacity=".6"/>
<g class="pb-x p-band"><path d="M95 132q55-15 110 0" stroke="#E24B4A" stroke-width="6" fill="none" stroke-linecap="round"/></g>
<g class="pb-x p-sweatrun" fill="#85B7EB"><path class="pb-drip" d="M98 132q3 5 0 7q-3-2 0-7z"/><path class="pb-drip" d="M203 130q3 5 0 7q-3-2 0-7z" style="animation-delay:.3s"/><path class="pb-drip" d="M120 118q3 5 0 7q-3-2 0-7z" style="animation-delay:.6s"/><path class="pb-drip" d="M184 116q3 5 0 7q-3-2 0-7z" style="animation-delay:.85s"/></g>
<g class="pb-x p-meditate"><ellipse cx="150" cy="106" rx="30" ry="12" fill="#6b7280"/><ellipse cx="150" cy="88" rx="20" ry="9" fill="#4b5563"/></g>
<g class="pb-x f-normal" id="pb-fNormal"><g class="pb-blink"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><ellipse cx="166" cy="142" rx="8" ry="9" fill="#fff"/><circle cx="135" cy="144" r="4" fill="#0f172a"/><circle cx="167" cy="144" r="4" fill="#0f172a"/></g><path d="M143 158q7 6 14 0" stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"/></g>
<g class="pb-x f-hello"><g class="pb-blink"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><ellipse cx="166" cy="142" rx="8" ry="9" fill="#fff"/><circle cx="136" cy="142" r="4" fill="#0f172a"/><circle cx="168" cy="142" r="4" fill="#0f172a"/></g><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M126 128q8-5 16 0M158 128q8-5 16 0"/><path d="M140 155q10 12 20 0z" fill="#0f172a"/></g><g fill="#F09595" opacity=".6"><circle cx="122" cy="156" r="5"/><circle cx="178" cy="156" r="5"/></g></g>
<g class="pb-x f-strain"><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M127 140l12 4M173 140l-12 4"/><path d="M141 160h18"/></g><g fill="#F09595" opacity=".7"><circle cx="122" cy="156" r="5"/><circle cx="178" cy="156" r="5"/></g></g>
<g class="pb-x f-think"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><ellipse cx="166" cy="142" rx="8" ry="9" fill="#fff"/><circle cx="137" cy="139" r="4" fill="#0f172a"/><circle cx="169" cy="139" r="4" fill="#0f172a"/><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M126 129h14"/><path d="M158 128q8-7 16-1"/><path d="M142 160l14-3"/></g></g>
<g class="pb-x f-search"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><circle cx="135" cy="144" r="4" fill="#0f172a"/><circle cx="167" cy="140" r="13" fill="#fff"/><g class="pb-lpup"><circle cx="168" cy="141" r="7" fill="#0f172a"/><circle cx="171" cy="138" r="2" fill="#fff"/></g><circle cx="167" cy="140" r="17" fill="#B5D4F4" fill-opacity=".35" stroke="#444441" stroke-width="5"/><path d="M146 160q5 3 10 0" stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"/></g>
<g class="pb-x f-down"><g class="pb-blink"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><ellipse cx="166" cy="142" rx="8" ry="9" fill="#fff"/><circle cx="135" cy="147" r="4" fill="#0f172a"/><circle cx="167" cy="147" r="4" fill="#0f172a"/></g><path d="M144 160q6 3 12 0" stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"/></g>
<g class="pb-x f-read"><g class="pb-blink"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><ellipse cx="166" cy="142" rx="8" ry="9" fill="#fff"/><g id="pb-readPupils"><circle cx="134" cy="147" r="4" fill="#0f172a"/><circle cx="166" cy="147" r="4" fill="#0f172a"/></g></g><path d="M145 160q5 2 10 0" stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"/></g>
<g class="pb-x f-happy" stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M127 144q7-8 14 0M159 144q7-8 14 0"/><path d="M140 155q10 12 20 0z" fill="#0f172a"/></g>
<g class="pb-x f-shock"><ellipse cx="134" cy="141" rx="10" ry="12" fill="#fff"/><ellipse cx="166" cy="141" rx="10" ry="12" fill="#fff"/><circle cx="134" cy="141" r="2.5" fill="#0f172a"/><circle cx="166" cy="141" r="2.5" fill="#0f172a"/><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M124 124q10-6 18 0M158 124q8-6 18 0"/></g><ellipse cx="150" cy="164" rx="6" ry="8" fill="#0f172a"/></g>
<g class="pb-x f-calm" stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M127 143q7 5 14 0M159 143q7 5 14 0"/><path d="M144 158q6 4 12 0"/></g>
<g class="pb-x f-wink"><ellipse cx="134" cy="142" rx="8" ry="9" fill="#fff"/><circle cx="135" cy="143" r="4" fill="#0f172a"/><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M159 144q7-7 14 0"/><path d="M126 129q8-5 16 0M158 132l14-3"/><path d="M140 155q10 12 20 0z" fill="#0f172a"/></g><path class="pb-spark" d="M186 120l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="#EF9F27"/></g>
<g id="pb-fDrink" display="none"><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M127 143q7 5 14 0M159 143q7 5 14 0"/></g><circle id="pb-mouthO" cx="151" cy="160" r="3.5" fill="#0f172a"/></g>
<g id="pb-fRefresh" display="none"><g stroke="#0f172a" stroke-width="2.5" fill="none" stroke-linecap="round"><path d="M127 144q7-8 14 0M159 144q7-8 14 0"/><path d="M138 154q12 14 24 0z" fill="#0f172a"/></g><g fill="#F09595" opacity=".75"><circle cx="122" cy="154" r="5"/><circle cx="178" cy="154" r="5"/></g><g fill="#85B7EB"><path class="pb-spark" d="M206 110l3 8 8 3-8 3-3 8-3-8-8-3 8-3z"/><path class="pb-spark" d="M92 118l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" style="animation-delay:.3s"/></g></g>
<g class="pb-x p-error"><path class="pb-drop" d="M198 118q6 10 0 14q-6-4 0-14z" fill="#85B7EB"/></g>
<g class="pb-x p-read"><path d="M108 172l42 6 42-6v32l-42 6-42-6z" fill="#185FA5"/><path d="M112 172l38 5v28l-38-5z" fill="#FAF9F5"/><path d="M188 172l-38 5v28l38-5z" fill="#FAF9F5"/><use href="#pb-lines" transform="translate(150 177) scale(-1 1)"/><use href="#pb-lines" transform="translate(150 177)"/><g id="pb-flipPage"><path d="M150 177l36-5v28l-36 5z" fill="#FFFFFF" stroke="#B4B2A9" stroke-width="1"/><use href="#pb-lines" transform="translate(150 177)"/></g><line x1="150" y1="176" x2="150" y2="206" stroke="#0C447C" stroke-width="2.5"/></g>
<g class="pb-x pb-props"><g id="pb-p-washer" display="none"><rect x="-22" y="-54" width="44" height="54" rx="5" fill="#F1EFE8" stroke="#B4B2A9" stroke-width="1.5"/><rect x="-22" y="-54" width="44" height="11" rx="5" fill="#D3D1C7"/><circle cx="-13" cy="-48.5" r="2.3" fill="#888780"/><circle cx="-6" cy="-48.5" r="2.3" fill="#888780"/><rect x="6" y="-50.5" width="11" height="4" rx="1.5" fill="#85B7EB"/><circle cx="0" cy="-22" r="12" fill="#2C2C2A"/><g id="pb-wdoor"><circle cx="0" cy="-22" r="12.5" fill="#378ADD" fill-opacity=".45" stroke="#B4B2A9" stroke-width="3.5"/><path d="M-6 -26q4-4 9-1" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" opacity=".7"/></g></g><path id="pb-p-band" d="M-14 0q14-6 28 0" stroke="#E24B4A" stroke-width="6" fill="none" stroke-linecap="round" vector-effect="non-scaling-stroke" display="none"/><g id="pb-p-bar" display="none"><line x1="-76" y1="0" x2="76" y2="0" stroke="#444441" stroke-width="5"/><rect x="-90" y="-14" width="14" height="28" rx="3" fill="#2C2C2A"/><rect x="76" y="-14" width="14" height="28" rx="3" fill="#2C2C2A"/></g><g id="pb-p-sweat" display="none" fill="#85B7EB"><path class="pb-drip" d="M-10 2q2.5 4 0 6q-2.5-2 0-6z"/><path class="pb-drip" d="M0 4q2.5 4 0 6q-2.5-2 0-6z" style="animation-delay:.35s"/><path class="pb-drip" d="M10 2q2.5 4 0 6q-2.5-2 0-6z" style="animation-delay:.7s"/></g><ellipse id="pb-p-st1" rx="30" ry="12" fill="#6b7280" display="none"/><ellipse id="pb-p-st2" rx="20" ry="9" fill="#4b5563" display="none"/></g>
<g fill="none" stroke="#2f5fbe" stroke-width="6" stroke-linecap="round">
<g class="pb-x a-down"><path d="M96 152 Q84 168 88 178"/><path d="M204 152 Q216 168 212 178"/><use ${HAND} transform="translate(88 180) rotate(180)"/><use ${HAND} transform="translate(212 180) rotate(180)"/></g>
<g class="pb-x a-hello"><path d="M96 152 Q84 168 88 178"/><use ${HAND} transform="translate(88 180) rotate(180)"/><g class="pb-wave"><path d="M204 150 Q224 132 222 108"/><use ${HAND} transform="translate(222 104) rotate(10)"/></g></g>
<g class="pb-x a-think"><path d="M96 152 Q84 168 88 178"/><use ${HAND} transform="translate(88 180) rotate(180)"/><path d="M204 152 Q198 180 162 172"/><use ${HAND} transform="translate(158 170) rotate(-20)"/></g>
<g class="pb-x a-search"><path d="M96 152 Q84 168 88 178"/><use ${HAND} transform="translate(88 180) rotate(180)"/><line x1="179" y1="153" x2="196" y2="172" stroke="#444441" stroke-width="7"/><path d="M204 152 Q214 168 198 176"/><use ${HAND} transform="translate(196 174) rotate(-60)"/></g>
<g class="pb-x a-type"><path class="pb-tyL" d="M96 152 Q98 170 122 172"/><path class="pb-tyR" d="M204 152 Q202 170 178 172"/><g class="pb-hL"><use ${HAND} transform="translate(124 172)"/></g><g class="pb-hR"><use ${HAND} transform="translate(176 172)"/></g></g>
<g class="pb-x a-read"><path d="M96 152 Q98 182 110 196"/><use ${HAND} transform="translate(110 198) rotate(150)"/><path id="pb-armR" d="M204 152 Q206 180 188 190"/><use id="pb-handR" ${HAND} transform="translate(188 190) rotate(-150)"/></g>
<g class="pb-x a-up"><path d="M94 144 Q80 126 86 108"/><path d="M206 144 Q220 126 214 108"/><use ${HAND} transform="translate(86 104)"/><use ${HAND} transform="translate(214 104)"/></g>
<g class="pb-x a-shock"><path d="M96 150 Q80 132 104 126"/><path d="M204 150 Q220 132 196 126"/><use ${HAND} transform="translate(106 122) rotate(20)"/><use ${HAND} transform="translate(194 122) rotate(-20)"/></g>
<g class="pb-x a-run"><g class="pb-swL"><path d="M96 150 L86 172"/><use ${HAND} transform="translate(85 175) rotate(200)"/></g><g class="pb-swR"><path d="M204 150 L214 172"/><use ${HAND} transform="translate(215 175) rotate(160)"/></g></g>
<g class="pb-x a-flex"><path d="M204 150 Q224 150 240 144"/><path d="M240 144 L236 116"/><ellipse class="pb-pump" cx="223" cy="141" rx="9" ry="6.5" fill="#4f7fd9" stroke="#2f5fbe" stroke-width="2"/><use ${HAND} transform="translate(236 110) rotate(-8)"/></g>
<g class="pb-x a-lap"><path d="M96 154 Q100 182 136 188"/><path d="M204 154 Q200 182 164 188"/><use ${HAND} transform="translate(140 188) rotate(90)"/><use ${HAND} transform="translate(160 188) rotate(-90)"/></g>
<g class="pb-x a-lift"><g class="pb-bar"><line x1="74" y1="104" x2="226" y2="104" stroke="#444441" stroke-width="5"/><rect x="60" y="90" width="14" height="28" rx="3" fill="#2C2C2A" stroke="none"/><rect x="226" y="90" width="14" height="28" rx="3" fill="#2C2C2A" stroke="none"/></g><path class="pb-armLL" d="M96 152 Q86 128 112 106"/><path class="pb-armLR" d="M204 152 Q214 128 188 106"/><g class="pb-bar"><use ${HAND} transform="translate(112 106)"/><use ${HAND} transform="translate(188 106)"/></g></g>
<g class="pb-x a-water"><path d="M96 152 Q84 168 88 178"/><use ${HAND} transform="translate(88 180) rotate(180)"/><path id="pb-armW" d="M204 152 Q218 164 222 172"/>
<g id="pb-bottle" transform="translate(212 150)">
<circle class="pb-grip" cx="11" cy="23" r="7" fill="#4f7fd9" stroke="#2f5fbe" stroke-width="2"/>
<rect x="-10" y="0" width="20" height="40" rx="6" fill="#F1EFE8" stroke="#185FA5" stroke-width="2.5"/><rect id="pb-liq" x="-7.5" y="4" width="15" height="33" rx="4" fill="#185FA5" stroke="none"/><rect x="-5" y="8" width="3" height="22" rx="1.5" fill="#fff" opacity=".45" stroke="none"/><rect x="-6" y="-5" width="12" height="6" rx="2" fill="#0C447C" stroke="none"/>
<g class="pb-grip" fill="#4f7fd9" stroke="#2f5fbe" stroke-width="1.5"><rect x="-4" y="15" width="16" height="5" rx="2.5"/><rect x="-5" y="20.5" width="17" height="5" rx="2.5"/><rect x="-4" y="26" width="16" height="5" rx="2.5"/><ellipse cx="-11" cy="21" rx="3" ry="4.5"/></g>
</g>
<line id="pb-stream" x1="0" y1="0" x2="0" y2="0" stroke="#378ADD" stroke-width="3" display="none"/><use id="pb-freeH" ${HAND} display="none"/></g>
<g class="pb-x a-dyn"><path id="pb-dL" d="M96 152 Q84 168 88 178"/><path id="pb-dR" d="M204 152 Q216 168 212 178"/><use id="pb-dHL" ${HAND}/><use id="pb-dHR" ${HAND}/></g>
</g>
<g id="pb-bin" display="none"><path d="M-17 -36h34l-3 36h-28z" fill="#1D9E75"/><rect x="-19" y="-40" width="38" height="6" rx="2" fill="#0F6E56"/><g transform="translate(0 -17) scale(1.05)"><g><line x1="-5.5" y1="1.6" x2="-2.3" y2="-4" stroke="#E1F5EE" stroke-width="2.2" stroke-linecap="round"/><path d="M-0.4 -7.2L0.1 -3.4L-3.9 -5.7Z" fill="#E1F5EE"/></g><g transform="rotate(120)"><line x1="-5.5" y1="1.6" x2="-2.3" y2="-4" stroke="#E1F5EE" stroke-width="2.2" stroke-linecap="round"/><path d="M-0.4 -7.2L0.1 -3.4L-3.9 -5.7Z" fill="#E1F5EE"/></g><g transform="rotate(240)"><line x1="-5.5" y1="1.6" x2="-2.3" y2="-4" stroke="#E1F5EE" stroke-width="2.2" stroke-linecap="round"/><path d="M-0.4 -7.2L0.1 -3.4L-3.9 -5.7Z" fill="#E1F5EE"/></g></g></g>
<g class="pb-x p-type"><rect x="108" y="160" width="84" height="30" rx="4" fill="#5F5E5A"/><rect x="112" y="164" width="76" height="22" rx="2" fill="#444441"/><circle cx="150" cy="175" r="4" fill="#85B7EB"/><path d="M100 206l8-14h84l8 14z" fill="#2C2C2A"/></g>
</g></g>
</svg>`;

  const EGGS = ['run', 'lift', 'water', 'meditate'];
  const EGG_EVERY = 8000;  // ms of idle before the next easter egg
  const REPS = 10, REP_MS = 1200;  // weights: ten reps overhead, then the throw
  const LEN = { done: 2600, error: 3200 };
  const ease = x => x < .5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
  const lerp = (a, b, k) => a + (b - a) * k;
  let svg, timer, raf, eggTimer, lastEgg, state = 'idle', visible = () => true;
  const $ = id => svg.querySelector('#' + id);

  function cls(s) { svg.setAttribute('class', ''); void svg.getBBox(); svg.setAttribute('class', 's-' + s); }

  // drinking: the bottle pops up on the ground, Pebble picks it up and drinks it empty (stream, gulps, glug),
  // then a recycling bin pops up, the bottle goes in, the bin goes away, and Pebble looks refreshed
  const GROUND = { x: 238, y: 166, a: 0 }, REST = { x: 212, y: 150, a: 0 }, MOUTH = { x: 156, y: 158, a: -128 };
  const ABOVE_BIN = { x: 264, y: 118, a: 0 }, IN_BIN = { x: 264, y: 172, a: 0 };
  const mixP = (A, B, k) => ({ x: lerp(A.x, B.x, k), y: lerp(A.y, B.y, k), a: lerp(A.a, B.a, k) });
  const gripOf = p => { const r = p.a * Math.PI / 180; return [p.x + Math.cos(r) * 13 - Math.sin(r) * 23, p.y + Math.sin(r) * 13 + Math.cos(r) * 23]; };
  // pop-in: grows a little past full size, then settles; pop-out: shrinks and fades
  const popIn = k => k < .7 ? 1.15 * (1 - Math.pow(1 - k / .7, 3)) : 1.15 - .15 * ease((k - .7) / .3);
  function grip(on) {
    svg.querySelectorAll('.pb-grip').forEach(e => e.style.display = on ? '' : 'none');
    $('pb-freeH').setAttribute('display', on ? 'none' : 'inline');
  }
  function face(n) {
    $('pb-fNormal').style.display = n === 'normal' ? 'inline' : 'none';
    $('pb-fDrink').style.display = n === 'drink' ? 'inline' : 'none';
    $('pb-fRefresh').style.display = n === 'refresh' ? 'inline' : 'none';
    $('pb-ahh').style.display = n === 'refresh' ? 'inline' : 'none';
  }
  function bin(scale, opacity = 1) {
    const b = $('pb-bin');
    b.setAttribute('display', scale > 0 ? 'inline' : 'none');
    b.setAttribute('transform', `translate(264 206) scale(${scale})`);
    b.setAttribute('opacity', opacity);
  }
  function water() {
    const PHASES = [['pop', 450], ['reach', 550], ['pick', 550], ['lift', 850], ['drink', 2800], ['lower', 850],
      ['binIn', 450], ['toss', 650], ['drop', 450], ['binOut', 400], ['refresh', 2800]];  // 10.8 s in all
    const t0 = performance.now();
    function step(now) {
      let e = now - t0, name = null, k = 0;
      for (const [n, d] of PHASES) { if (e < d) { name = n; k = e / d; break; } e -= d; }
      if (!name) { set('idle'); return; }
      const K = ease(k);
      let p = GROUND, hand = null, level = 1, f = 'normal', scale = 1, alpha = 1;
      if (name === 'pop') { grip(false); hand = HOME.R; scale = popIn(k); }
      else if (name === 'reach') { grip(false); hand = mixA(HOME.R, gripOf(GROUND), K); }
      else if (name === 'pick') { grip(true); p = mixP(GROUND, REST, K); }
      else if (name === 'lift') { p = mixP(REST, MOUTH, K); }
      else if (name === 'drink') { p = MOUTH; f = 'drink'; level = 1 - k; $('pb-mouthO').setAttribute('r', 3.5 + Math.abs(Math.sin(now / 120)) * 1.5); }
      else if (name === 'lower') { p = mixP(MOUTH, REST, K); level = 0; f = 'drink'; }
      else if (name === 'binIn') { p = REST; level = 0; bin(popIn(k)); }
      else if (name === 'toss') { p = mixP(REST, ABOVE_BIN, K); level = 0; bin(1); }
      else if (name === 'drop') {  // let go: the bottle falls into the bin, the hand goes home
        level = 0; grip(false); bin(1);
        p = mixP(ABOVE_BIN, IN_BIN, k * k); alpha = k < .75 ? 1 : 1 - (k - .75) / .25;
        hand = mixA(gripOf(ABOVE_BIN), HOME.R, K);
      }
      else if (name === 'binOut') { level = 0; grip(false); hand = HOME.R; alpha = 0; bin(1 - .4 * K, 1 - K); }
      else { level = 0; grip(false); hand = HOME.R; alpha = 0; bin(0); f = 'refresh'; }
      const bt = $('pb-bottle');
      bt.setAttribute('transform', `translate(${p.x} ${p.y}) rotate(${p.a}) translate(0 20) scale(${scale}) translate(0 -20)`);
      bt.setAttribute('opacity', alpha);
      const h = hand || gripOf(p);
      $('pb-armW').setAttribute('d', arm('R', h));
      if (hand) handAt($('pb-freeH'), 'R', hand);
      face(f);
      $('pb-liq').setAttribute('height', 33 * Math.max(0, level));
      const drinking = name === 'drink', l = $('pb-stream');
      l.setAttribute('display', drinking ? 'inline' : 'none');
      $('pb-glug').setAttribute('display', drinking ? 'inline' : 'none');
      if (drinking) {
        const r = MOUTH.a * Math.PI / 180;
        l.setAttribute('x1', MOUTH.x + Math.sin(r) * 3); l.setAttribute('y1', MOUTH.y - Math.cos(r) * 3);
        l.setAttribute('x2', 151); l.setAttribute('y2', 160);
        $('pb-glug').setAttribute('opacity', .4 + .6 * Math.abs(Math.sin(now / 250)));
      }
      raf = requestAnimationFrame(step);
    }
    $('pb-liq').setAttribute('height', 33);
    raf = requestAnimationFrame(step);
  }

  // ---------- scenes: the idle routines' start and finish (getting the prop, putting it away) ----------
  // Arms are drawn freely here: each frame says where the hands go; props (headband, barbell, stones) can sit
  // anywhere, be carried, or be hidden. frames[0] is the starting pose; each later frame is reached over t ms.
  const SH = { L: [96, 152], R: [204, 152] }, HOME = { L: [88, 180], R: [212, 180] };
  const PROPS = ['base', 'washer', 'band', 'sweat', 'bar', 'st1', 'st2'];
  const mixA = (a, b, k) => a.map((v, i) => lerp(v, b[i] ?? v, k));
  const pad = q => [q[0], q[1], q[2] ?? 0, q[3] ?? 1, q[4] ?? 1];  // prop: x, y, rotation, scale, opacity
  const EASE = { lin: k => k, in: k => k * k, out: k => 1 - (1 - k) * (1 - k) };
  const bend = (side, [hx, hy]) => { const [sx, sy] = SH[side]; return [(sx + hx) / 2 + (side === 'L' ? -12 : 12), (sy + hy) / 2 + 10]; };
  function arm(side, h, c = bend(side, h)) {
    const [sx, sy] = SH[side];
    return `M${sx} ${sy} Q${c[0]} ${c[1]} ${h[0]} ${h[1]}`;
  }
  function handAt(el, side, [hx, hy]) {
    const [sx, sy] = SH[side];
    el.setAttribute('transform', `translate(${hx} ${hy}) rotate(${Math.atan2(hy - sy, hx - sx) * 180 / Math.PI + 90})`);
  }
  function scene(frames, then) {
    const full = [];
    let prev = { L: HOME.L, R: HOME.R, face: 'normal', hb: false, by: 0 };
    for (const f of frames) { prev = { ...prev, ease: undefined, ...f }; full.push(prev); }  // Rc (elbow) carries over like hands
    let i = 0, t0 = performance.now();
    const step = now => {
      const a = full[Math.max(0, i - 1)], b = full[i];
      const k = b.t ? (EASE[b.ease] || ease)(Math.min(1, (now - t0) / b.t)) : 1;
      svg.setAttribute('class', `s-dyn fx-${b.face}` + ['hb', 'backR', 'flex', 'cross', 'breathe', 'sweaty'].map(f => b[f] ? ' ' + f : '').join(''));
      $('pb-lift').setAttribute('transform', `translate(0 ${lerp(a.by, b.by, k)})`);  // Pebble's whole body can rise and land
      if (b.door !== undefined) {  // washing-machine door: 0 closed … 1 swung open on its left hinge
        const d = lerp(a.door ?? b.door, b.door, k);
        $('pb-wdoor').setAttribute('transform', `translate(-14 0) scale(${1 - 1.75 * d} 1) translate(14 0)`);
      }
      const L = mixA(a.L, b.L, k), R = mixA(a.R, b.R, k);
      const Rc = mixA(a.Rc || bend('R', a.R), b.Rc || bend('R', b.R), k);  // the right elbow can be posed (for the flex)
      $('pb-dL').setAttribute('d', arm('L', L)); $('pb-dR').setAttribute('d', arm('R', R, Rc));
      handAt($('pb-dHL'), 'L', L); handAt($('pb-dHR'), 'R', R);
      $('pb-dRb').setAttribute('d', arm('R', R)); handAt($('pb-dHRb'), 'R', R);  // the same arm, drawn behind the body
      for (const name of PROPS) {
        const el = $('pb-p-' + name), to = b[name], from = a[name];
        if (!to) { el.setAttribute('display', 'none'); continue; }
        const [x, y, r, sc, op] = from ? mixA(pad(from), pad(to), k) : pad(to);
        el.setAttribute('display', 'inline');
        el.setAttribute('opacity', op);
        el.setAttribute('transform', `translate(${x} ${y}) rotate(${r}) scale(${sc})`);
      }
      if (k >= 1) {
        i++; t0 = now;
        if (i >= full.length) { then(); return; }
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }
  const later = (ms, fn) => { timer = setTimeout(fn, ms); };

  // jogging (≈18.5 s): the right arm reaches behind the body until the hand is hidden, comes back round the side
  // and the headband pops into its hand; to the chest, both hands stretch it, onto the head; jog; then a washing
  // machine pops up, Pebble takes the sweaty, dripping headband off and puts it in the machine, which gives a
  // little wash wiggle and goes away
  function runScene() {
    const BEHIND = [182, 142], SIDE = [216, 136], CHEST = [166, 166], HEAD_L = [104, 126], HEAD_R = [196, 126];
    const WASHER = [46, 206], DOOR = [46, 182];
    const held = (h, sc = 1, op = 1) => [h[0], h[1] - 5, 0, sc, op];
    const drips = (h, op = 1) => [h[0], h[1] - 2, 0, 1, op];
    scene([{ t: 0 },
      { t: 650, R: BEHIND, backR: true },                                   // reach behind the back (hand disappears)
      { t: 380, R: SIDE, band: held(SIDE, 0, 0) },                           // round the side…
      { t: 200, band: held(SIDE, 1.2, 1), ease: 'out' }, { t: 120, band: held(SIDE) },  // …and the headband pops into its hand
      { t: 450, R: CHEST, backR: false, band: held(CHEST) },
      { t: 400, L: [124, 164], R: [176, 164], band: [150, 162, 0, 1.6] },    // both hands take the ends
      { t: 520, L: [108, 162], R: [192, 162], band: [150, 162, 0, 3] },      // stretch it out
      { t: 550, L: HEAD_L, R: HEAD_R, band: [150, 129, 0, 3.9] },            // up onto the head
      { t: 200, band: null, hb: true },
      { t: 400, L: HOME.L, R: HOME.R }], () => {
      cls('run');
      later(10350, () => scene([{ t: 0, hb: true, washer: [...WASHER, 0, 0, 0], door: 1 },
        { t: 300, washer: [...WASHER, 0, 1.12, 1], ease: 'out' }, { t: 150, washer: [...WASHER, 0, 1, 1] },  // the machine pops up
        { t: 450, L: HEAD_L, R: HEAD_R },
        { t: 0, hb: false, band: [150, 129, 0, 3.9], sweat: drips([150, 129]) },                             // off, dripping
        { t: 550, L: [118, 160], R: [182, 160], band: [150, 160, 0, 2.4], sweat: drips([150, 160]) },
        { t: 380, L: [134, 166], R: HOME.R, band: held([134, 166]), sweat: drips([134, 162]) },             // into the left hand
        { t: 550, L: [62, 182], band: held([62, 182], .9), sweat: drips([62, 178]) },                         // to the door
        { t: 420, L: [58, 182], band: [...DOOR, 0, .4, 0], sweat: drips(DOOR, 0) },                          // in it goes
        { t: 0, band: null, sweat: null },
        { t: 380, L: [42, 182], door: .15 }, { t: 160, door: 0 },                                         // shut the door
        { t: 140, L: HOME.L, washer: [...WASHER, -4, 1, 1] }, { t: 140, washer: [...WASHER, 4, 1, 1] },    // wash wiggle
        { t: 140, washer: [...WASHER, -3, 1, 1] }, { t: 140, washer: [...WASHER, 0, 1, 1] },
        { t: 420, washer: [...WASHER, 0, .5, 0] }, { t: 0, washer: null }], () => set('idle')));
    });
  }
  // weights (≈17 s): the barbell pops up on the floor; grab, chest, overhead; 10 reps; wind back, then one smooth
  // throw: the bar leaves the hands and flies a single arc (up, over, down off-screen) spinning steadily
  function liftScene() {
    const FLOOR = [150, 198, 0];
    const grips = (x, y, r) => { const c = Math.cos(r * Math.PI / 180) * 38, d = Math.sin(r * Math.PI / 180) * 38; return { L: [x - c, y - d], R: [x + c, y + d] }; };
    const throwFrames = () => {
      // bar path over the whole throw: x moves steadily right, y rises then falls (a parabola), rotation spins on
      const X0 = 134, Y0 = 100, R0 = -14, N = 18, DUR = 1300, RELEASE = .22, FT_L = [126, 126], FT_R = [222, 104];
      const at = t => [X0 + 210 * t, Y0 - 80 * t + 210 * t * t, R0 + 230 * t];
      const released = grips(...at(RELEASE)), out = [];
      for (let i = 1; i <= N; i++) {
        const t = i / N, [x, y, r] = at(t);
        const f = { t: DUR / N, ease: 'lin', bar: [x, y, r] };
        if (t <= RELEASE) Object.assign(f, grips(x, y, r));                   // hands still on the bar
        else {                                                                 // let go: hands follow through
          const k = ease(Math.min(1, (t - RELEASE) / .35));
          f.L = mixA(released.L, FT_L, k); f.R = mixA(released.R, FT_R, k);
        }
        if (i === Math.ceil(RELEASE * N)) f.face = 'happy';
        out.push(f);
      }
      return out;
    };
    scene([{ t: 0, bar: [...FLOOR, 0, 0] }, { t: 300, bar: [...FLOOR, 1.12, 1], ease: 'out' }, { t: 150, bar: [...FLOOR, 1, 1] },
      { t: 600, L: [112, 196], R: [188, 196] },
      { t: 700, L: [112, 150], R: [188, 150], bar: [150, 150, 0], face: 'strain' },
      { t: 600, L: [112, 106], R: [188, 106], bar: [150, 104, 0] }], () => {
      cls('lift');
      later(REPS * REP_MS, () => scene([{ t: 0, L: [112, 106], R: [188, 106], bar: [150, 104, 0], face: 'strain', sweaty: true },
        { t: 600, ...grips(134, 100, -14), bar: [134, 100, -14] },             // wind back
        ...throwFrames(), { t: 0, bar: null },
        { t: 320, L: HOME.L, R: [214, 128], face: 'happy', sweaty: false },    // settle after the throw
        { t: 650, R: [236, 112], Rc: [248, 152] },                             // bend the arm up into the flex shape
        { t: 0, flex: true, face: 'wink' }, { t: 1700 },                        // …and flex, with a wink
        { t: 0, flex: false, face: 'happy' }, { t: 550, R: HOME.R, Rc: bend('R', HOME.R), face: 'normal' }], () => set('idle')));
    });
  }
  // balancing stones (≈20 s), like the StoneDock logo: Pebble hops up and the big grey stone pops in underneath,
  // it lands sitting cross-legged on top, a small stone pops up beside it and goes on its head (three stones,
  // grey, blue, grey); it meditates, breathing slowly; then the small stone goes back down, it hops off, and the
  // stones shrink away. Positions are in Pebble's own coordinates, which rise with it (by = how far up it sits).
  function meditateScene() {
    // sitting 12 up puts the crossed legs (bottom at 197) just onto the big stone's top (182); the small stone's
    // spot on the ground (y 202) is 214 in Pebble's raised coordinates
    const SIT = -12, BASE = [150, 199, 0], SMALL_GROUND = [256, 214, 0], ON_HEAD = [150, 103, 0];
    scene([{ t: 0, base: [...BASE, 0, 0] },
      { t: 300, by: -40, L: [82, 146], R: [218, 146], ease: 'out' },          // hop up, arms out
      { t: 260, base: [...BASE, 1.12, 1], ease: 'out' }, { t: 120, base: [...BASE, 1, 1] },  // the big stone pops in below
      { t: 300, by: SIT, cross: true, L: HOME.L, R: HOME.R, ease: 'in' },     // land, sitting cross-legged on top
      { t: 0, st2: [...SMALL_GROUND, 0, 0] },
      { t: 260, st2: [...SMALL_GROUND, 1.15, 1], ease: 'out' }, { t: 120, st2: [...SMALL_GROUND, 1, 1] },
      { t: 450, R: [256, 201] },                                              // reach for the small stone
      { t: 700, R: [150, 89], st2: ON_HEAD },                                 // onto its head: the logo stack
      { t: 420, L: [140, 188], R: [160, 188], face: 'calm', breathe: true },  // hands in lap
      { t: 13800 },                                                           // meditate
      { t: 420, L: HOME.L, R: [150, 89], face: 'normal', breathe: false },
      { t: 650, R: [256, 201], st2: SMALL_GROUND },                           // the small stone goes back down
      { t: 350, R: HOME.R },
      { t: 300, by: -40, cross: false, base: [...BASE, .5, 0], ease: 'out' }, // hop off; the big stone shrinks away
      { t: 300, by: 0, ease: 'in' },
      { t: 400, st2: [...SMALL_GROUND, .5, 0] }, { t: 0, base: null, st2: null }], () => set('idle'));
  }
  const SCENES = { run: runScene, lift: liftScene, meditate: meditateScene };

  // reading: eyes sweep the lines, the right hand carries each page over the spine, then reaches back for the next
  function read() {
    const t0 = performance.now(), READ = 1600, TURN = 1500, BACK = 800, CYCLE = READ + TURN + BACK;
    function step(now) {
      const c = (now - t0) % CYCLE;
      let p = 0, hx = 188, hy = 190, ang = -150;
      if (c < READ) $('pb-readPupils').setAttribute('transform', `translate(${-3 + 6 * ((c / READ * 2) % 1)} 0)`);
      else if (c < READ + TURN) { p = ease((c - READ) / TURN); $('pb-readPupils').setAttribute('transform', `translate(${3 - 6 * p} 0)`); }
      else { const k = ease((c - READ - TURN) / BACK); hx = lerp(114, 188, k); hy = lerp(186, 190, k); ang = lerp(-30, -150, k); }
      const s = Math.cos(Math.PI * p), lift = Math.sin(Math.PI * p), b = -0.15 * lift;
      $('pb-flipPage').setAttribute('transform', `matrix(${s} ${b} 0 1 ${150 * (1 - s)} ${-b * 150})`);
      if (c >= READ && c < READ + TURN) { hx = 150 + 36 * s; hy = 186 + b * 36; ang = lerp(-150, -30, p); }
      $('pb-handR').setAttribute('transform', `translate(${hx} ${hy}) rotate(${ang})`);
      $('pb-armR').setAttribute('d', `M204 152 Q${lerp(208, 190, lift)} ${lerp(180, 150, lift)} ${hx} ${hy}`);
      raf = requestAnimationFrame(step);
    }
    raf = requestAnimationFrame(step);
  }

  function scheduleEgg() {
    clearTimeout(eggTimer);
    eggTimer = setTimeout(() => {
      if (state === 'idle' && visible()) {
        const pick = EGGS.filter(e => e !== lastEgg);  // never the same routine twice in a row
        lastEgg = pick[Math.floor(Math.random() * pick.length)];
        set(lastEgg);
      }
      else scheduleEgg();
    }, EGG_EVERY);
  }

  function set(s) {
    if (!svg) return;
    cancelAnimationFrame(raf); clearTimeout(timer);
    state = s;
    cls(s);
    ['pb-fDrink', 'pb-fRefresh'].forEach(i => $(i).style.display = 'none');
    PROPS.forEach(n => $('pb-p-' + n).setAttribute('display', 'none'));
    svg.querySelectorAll('.pb-grip').forEach(e => e.style.display = '');
    $('pb-freeH').setAttribute('display', 'none');
    $('pb-bin').setAttribute('display', 'none');
    $('pb-lift').removeAttribute('transform');
    $('pb-wdoor').removeAttribute('transform');
    $('pb-bottle').setAttribute('opacity', 1);
    $('pb-fNormal').style.display = ''; $('pb-ahh').style.display = 'none'; $('pb-glug').setAttribute('display', 'none');
    if (s === 'hello') timer = setTimeout(() => { cls('hello2'); timer = setTimeout(() => set('idle'), 3200); }, 2300);
    else if (s === 'water') water();
    else if (SCENES[s]) SCENES[s]();
    else if (s === 'read') read();
    else if (LEN[s]) timer = setTimeout(() => set('idle'), LEN[s]);
    if (s === 'idle') scheduleEgg();
  }

  // a sequence of work states for a long request: e.g. ['think', 2000, 'search', 6000, 'read', 6000] loops the tail
  let workTimer;
  function work(steps) {
    clearTimeout(workTimer);
    let i = 0;
    const next = () => {
      if (i >= steps.length) i = steps.length > 2 ? 2 : 0;  // keep cycling the steps after the first
      const s = steps[i], ms = steps[i + 1];
      i += 2;
      set(s);
      workTimer = setTimeout(next, ms);
    };
    next();
  }
  function finish(ok) { clearTimeout(workTimer); set(ok ? 'done' : 'error'); }

  function mount(el, isVisible) {
    if (!document.getElementById('pbCss')) {
      const st = document.createElement('style');
      st.id = 'pbCss'; st.textContent = CSS;
      document.head.appendChild(st);
    }
    el.innerHTML = SVG;
    svg = el.querySelector('#pbSvg');
    if (isVisible) visible = isVisible;
    set('idle');
  }

  window.Pebble = { mount, set, work, finish, hello: () => set('hello'), get state() { return state; } };
})();
