function svgEl(tag, attrs = {}) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k,v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function clear(svg){ while(svg.firstChild) svg.removeChild(svg.firstChild); }

function renderLegend(container, items){
  container.innerHTML = "";
  for(const it of items){
    const span = document.createElement("div");
    span.innerHTML = `<span class="dot" style="background:${it.color}"></span>${it.label}: <b>${it.value}%</b>`;
    container.appendChild(span);
  }
}

function drawDonut(svg, items){
  clear(svg);
  const cx=150, cy=150, r=95, stroke=36;
  const total = items.reduce((a,b)=>a+(Number(b.value)||0),0) || 1;
  let start = -90;

  // faint base ring
  svg.appendChild(svgEl("circle",{cx,cy,r,fill:"none",stroke:"rgba(255,255,255,.08)","stroke-width":stroke}));

  for(const it of items){
    const v = Number(it.value)||0;
    const sweep = (v/total) * 360;
    const end = start + sweep;
    const path = arcPath(cx,cy,r,start,end);
    svg.appendChild(svgEl("path",{
      d: path,
      fill:"none",
      stroke: it.color || "#999",
      "stroke-width": stroke,
      "stroke-linecap":"butt"
    }));
    start = end;
  }

  // center cutout
  svg.appendChild(svgEl("circle",{cx,cy,r:r-stroke/2,fill:"rgba(17,21,29,.92)"}));
}

function arcPath(cx,cy,r,startDeg,endDeg){
  const toRad = d => d*Math.PI/180;
  const x1 = cx + r*Math.cos(toRad(startDeg));
  const y1 = cy + r*Math.sin(toRad(startDeg));
  const x2 = cx + r*Math.cos(toRad(endDeg));
  const y2 = cy + r*Math.sin(toRad(endDeg));
  const large = (endDeg-startDeg) > 180 ? 1 : 0;
  return `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`;
}

/**
 * drawBars(svg, labels, values, colors?)
 * colors can be null or array matching values length
 */
function drawBars(svg, labels, values, colors=null){
  clear(svg);
  const W=520,H=240, pad=34, base=200;
  const vals = (values||[]).map(v=>Number(v)||0);
  const max = Math.max(...vals, 1);

  // grid
  for(let i=0;i<4;i++){
    const y = pad + i*40;
    svg.appendChild(svgEl("line",{x1:pad,y1:y,x2:W-pad,y2:y,stroke:"rgba(255,255,255,.10)"}));
  }

  const n = vals.length || 1;
  const gap = 10;
  const barW = (W - pad*2 - gap*(n-1)) / n;

  vals.forEach((v,i)=>{
    const h = (v/max) * 110;
    const x = pad + i*(barW+gap);
    const y = base - h;
    const fill = (colors && colors[i]) ? colors[i] : "rgba(255,138,0,.95)";
    svg.appendChild(svgEl("rect",{x,y,width:barW,height:h,rx:4,fill}));
    const t = svgEl("text",{x:x+barW/2,y:225,"text-anchor":"middle",fill:"rgba(255,255,255,.65)","font-size":"12"});
    t.textContent = labels[i] ?? "";
    svg.appendChild(t);
  });
}

function renderTable(container, rows){
  container.innerHTML = "";
  for(const r of (rows||[])){
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<div class="k">${r.k ?? ""}</div><div class="v">${r.v ?? ""}</div>`;
    container.appendChild(row);
  }
}

function drawLines(svg, lines){
  clear(svg);
  const W=520,H=320, padL=44, padT=20, padB=44, padR=18;
  const plotW = W-padL-padR;
  const plotH = H-padT-padB;

  // grid
  for(let i=0;i<5;i++){
    const y = padT + i*(plotH/4);
    svg.appendChild(svgEl("line",{x1:padL,y1:y,x2:W-padR,y2:y,stroke:"rgba(255,255,255,.10)"}));
  }

  const series = (lines && lines.series) ? lines.series : [];
  const xlabels = (lines && lines.x_labels) ? lines.x_labels : [];
  const all = series.flatMap(s => (s.points||[]).map(v=>Number(v)||0));
  const min = all.length ? Math.min(...all) : 0;
  const max = all.length ? Math.max(...all) : 1;
  const span = (max-min) || 1;

  const xFor = (i,n)=> padL + (n<=1?0:(i*(plotW/(n-1))));
  const yFor = (v)=> padT + (plotH - ((v-min)/span)*plotH);

  series.forEach(s=>{
    const pts = (s.points||[]).map(v=>Number(v)||0);
    const n = pts.length || 1;
    let d = "";
    pts.forEach((v,i)=>{
      const x = xFor(i,n), y = yFor(v);
      d += (i===0?`M ${x} ${y}`:` L ${x} ${y}`);
    });
    svg.appendChild(svgEl("path",{d,fill:"none",stroke:(s.color||"#FF8A00"),"stroke-width":"3"}));
    pts.forEach((v,i)=>{
      const x=xFor(i,n), y=yFor(v);
      svg.appendChild(svgEl("circle",{cx:x,cy:y,r:4,fill:"#ffffff"}));
      svg.appendChild(svgEl("circle",{cx:x,cy:y,r:3,fill:(s.color||"#FF8A00")}));
    });
  });

  const n = xlabels.length || 0;
  xlabels.forEach((lab,i)=>{
    const x = xFor(i,n);
    const t = svgEl("text",{x, y:H-18, "text-anchor":"middle", fill:"rgba(255,255,255,.55)","font-size":"12"});
    t.textContent = lab;
    svg.appendChild(t);
  });
}
