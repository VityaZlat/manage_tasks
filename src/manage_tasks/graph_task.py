import os
import re
import json
import base64
import webbrowser

TREE_FILE = 'source/tree.txt'
HTML_FILE = 'tasks.html'
IMG_FILE = 'source/msu.jpg'


# ---------- parsing ----------

def parse(text):
    out = []
    section = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = re.match(r'^(\d+(?:\.\d+)*)\.?\s+(.+)$', s)
        if m:
            out.append((m.group(1), m.group(2).strip(), section))
        else:
            section = s
            out.append((None, s, None))
    return out


def build_nodes(entries):
    nodes = []
    idx = {}
    section_root = {}

    for num, label, section in entries:
        if num is None:
            node = {
                'id': label,
                'label': label,
                'section': label,
                'parent': -1,
                'children': [],
                'level': 0,
                'slot': 0,
                'root_index': -1,
                'is_section': True,
                'ring': 0,
                'angle': 0.0,
            }
            section_root[label] = len(nodes)
            nodes.append(node)
        else:
            node = {
                'id': num,
                'label': label,
                'section': section or '',
                'parent': -1,
                'children': [],
                'level': 0,
                'slot': 0,
                'root_index': -1,
                'is_section': False,
                'ring': 0,
                'angle': 0.0,
            }
            idx[(section or '', num)] = len(nodes)
            nodes.append(node)

    for i, n in enumerate(nodes):
        if n['is_section']:
            continue
        if '.' in n['id']:
            pnum = n['id'].rsplit('.', 1)[0]
            pi = idx.get((n['section'], pnum), -1)
            if pi >= 0:
                n['parent'] = pi
                nodes[pi]['children'].append(i)
        else:
            if n['section'] and n['section'] in section_root:
                pi = section_root[n['section']]
                n['parent'] = pi
                nodes[pi]['children'].append(i)

    roots = [i for i, n in enumerate(nodes) if n['parent'] == -1]
    if not roots:
        return nodes

    for r in roots:
        nodes[r]['level'] = 0
    q = list(roots)
    while q:
        v = q.pop(0)
        for c in nodes[v]['children']:
            nodes[c]['level'] = nodes[v]['level'] + 1
            q.append(c)

    color_counter = [0]

    def assign_color(v, inherited):
        n = nodes[v]
        if n['is_section']:
            n['root_index'] = -1
            for c in n['children']:
                assign_color(c, -1)
        elif inherited >= 0:
            n['root_index'] = inherited
            for c in n['children']:
                assign_color(c, inherited)
        else:
            my = color_counter[0]
            color_counter[0] += 1
            n['root_index'] = my
            for c in n['children']:
                assign_color(c, my)

    for r in roots:
        assign_color(r, -1)

    # Polar coordinates (ring + angle) for every root's subtree.
    PI = 3.14159265358979
    for r in roots:
        by_ring = {}
        queue = [[r, 0]]
        while queue:
            it = queue.pop(0)
            v, ring = it[0], it[1]
            if v != r:
                if ring not in by_ring:
                    by_ring[ring] = []
                by_ring[ring].append(v)
            for c in nodes[v]['children']:
                queue.append([c, ring + 1])
        for ring in by_ring:
            arr = by_ring[ring]
            n = len(arr)
            for k in range(n):
                v = arr[k]
                nodes[v]['ring'] = ring
                nodes[v]['angle'] = -PI / 2 + (2 * PI * k) / n
        nodes[r]['ring'] = 0
        nodes[r]['angle'] = 0.0

    return nodes


# ---------- image ----------

def embed_image(path):
    """Return a data-URI string for the image, or an empty string."""
    if not path or not os.path.exists(path):
        return ''
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except Exception:
        return ''
    ext = os.path.splitext(path)[1].lower()
    if ext == '.png':
        mime = 'image/png'
    elif ext in ('.jpg', '.jpeg'):
        mime = 'image/jpeg'
    elif ext == '.gif':
        mime = 'image/gif'
    elif ext == '.webp':
        mime = 'image/webp'
    else:
        mime = 'image/jpeg'
    b64 = base64.b64encode(raw).decode('ascii')
    return 'data:%s;base64,%s' % (mime, b64)


# ---------- html ----------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Task Tree</title>
<style>
  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    background: #0d1117; color: #e6edf3;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  canvas { display: block; cursor: grab; }
  canvas.drag { cursor: grabbing; }
  #hint {
    position: fixed; top: 12px; left: 12px;
    font-size: 13px; color: #8b949e;
    user-select: none; pointer-events: none; line-height: 1.5;
  }
  #hint b { color: #e6edf3; }
  #err {
    position: fixed; left: 12px; bottom: 12px; right: 12px;
    padding: 10px; background: #3a1010; color: #ffb4b4;
    border-radius: 6px; font: 12px/1.4 monospace;
    white-space: pre-wrap; display: none; z-index: 999;
  }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="hint">
  <b>Click a root</b> — expand/collapse its star<br>
  <b>Drag a node</b> — move it inside the star<br>
  <b>Drag a root</b> — move the whole star
</div>
<div id="err"></div>

<script>
window.onerror = function (msg, src, line) {
  var e = document.getElementById('err');
  e.style.display = 'block';
  e.textContent += 'JS Error: ' + msg + ' (line ' + line + ')\\n';
};

// ---- DATA FROM PYTHON ----
var DATA = __GRAPH_DATA__;
var BG_IMAGE = "__BG_IMAGE__";
// --------------------------

(function () {
  var canvas = document.getElementById('c');
  var ctx = canvas.getContext('2d');
  var W = 800, H = 600, DPR = 1;

  var FONT_MAX = 18;
  var FONT_MIN = 8;
  var FONT_BASE = 13;
  var FONT_BIG_FACTOR = 1.5;

  var PAD = 40;

  var ROOT_COLORS = [
    { fill: '#2d4a22', stroke: '#7ee787', text: '#c9f7b0' },
    { fill: '#4a2d22', stroke: '#ffa657', text: '#ffd8b0' },
    { fill: '#2d224a', stroke: '#a78bfa', text: '#ddd0ff' },
    { fill: '#224a4a', stroke: '#5eead4', text: '#b0fff5' },
    { fill: '#4a2240', stroke: '#f472b6', text: '#ffc9e5' },
    { fill: '#4a4522', stroke: '#facc15', text: '#fff3a0' },
    { fill: '#22384a', stroke: '#60a5fa', text: '#bcd8ff' },
    { fill: '#4a2222', stroke: '#f87171', text: '#ffc0c0' }
  ];

  var ROOT_FILL = '#1f2937';
  var ROOT_STROKE = '#cbd5e1';
  var ROOT_TEXT = '#f1f5f9';

  function branchColor(rootIndex) {
    if (rootIndex < 0) rootIndex = 0;
    return ROOT_COLORS[rootIndex % ROOT_COLORS.length];
  }

  // Background image (may be empty).
  var bgImg = null;
  var bgReady = false;
  if (BG_IMAGE) {
    bgImg = new Image();
    bgImg.onload = function () { bgReady = true; };
    bgImg.onerror = function () { bgReady = false; };
    bgImg.src = BG_IMAGE;
  }

  // Paint the background: solid color + faded image (cover, centered).
  function paintBackground() {
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, W, H);

    if (!bgReady || !bgImg || !bgImg.width) return;

    var iw = bgImg.width, ih = bgImg.height;
    var scale = Math.max(W / iw, H / ih);
    var dw = iw * scale;
    var dh = ih * scale;
    var dx = (W - dw) / 2;
    var dy = (H - dh) / 2;

    // Two-step fade: low global alpha + desaturation via 'luminosity'
    // over a dark overlay. Simple and reliable across browsers.
    ctx.save();
    ctx.globalAlpha = 3*0.16;
    ctx.drawImage(bgImg, dx, dy, dw, dh);
    ctx.restore();

    // Darkening + slight contrast reduction overlay.
    ctx.save();
    ctx.fillStyle = '#0d1117';
    ctx.globalAlpha = 0.5*0.45;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth || 800;
    H = window.innerHeight || 600;
    canvas.width = Math.floor(W * DPR);
    canvas.height = Math.floor(H * DPR);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  if (!DATA || DATA.length === 0) {
    var e = document.getElementById('err');
    e.style.display = 'block';
    e.textContent = 'No data: DATA array is empty.';
    return;
  }

  // ---- build ----
  var nodes = [];
  for (var i = 0; i < DATA.length; i++) {
    var d = DATA[i];
    var isSection = !!d.is_section;
    nodes.push({
      id: d.id,
      label: d.label,
      section: d.section || '',
      text: isSection ? d.label : (d.id + '. ' + d.label),
      parent: d.parent,
      children: d.children,
      level: d.level,
      slot: d.slot,
      ring: d.ring || 0,
      angle: d.angle || 0,
      rootIndex: (d.root_index === undefined ? -1 : d.root_index),
      isSection: isSection,
      x: 0, y: 0, vx: 0, vy: 0,
      tx: 0, ty: 0,
      userDx: 0, userDy: 0,
      w: 100, h: 32,
      fontSize: FONT_BASE, targetSize: FONT_BASE,
      expanded: false,
      isRoot: false,
      dragging: false,
      _idx: i
    });
  }

  var edges = [];
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].parent >= 0) edges.push([nodes[i].parent, i]);
  }

  var roots = [];
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].parent === -1) {
      roots.push(i);
      nodes[i].isRoot = true;
    }
  }

  function rootOf(v) {
    var cur = v;
    while (nodes[cur].parent >= 0) cur = nodes[cur].parent;
    return cur;
  }

  function measure(n, fs) {
    ctx.font = '600 ' + fs.toFixed(1) + 'px system-ui, sans-serif';
    var tw = ctx.measureText(n.text).width;
    n.w = Math.ceil(tw + fs * 1.6 + 14);
    n.h = Math.ceil(fs * 1.8 + 14);
  }

  // ---- layout ----

  var RING_STEP = 100;

  function starMaxRadius(rootIdx) {
    var maxRing = 0;
    var q = [[rootIdx, 0]];
    while (q.length) {
      var it = q.shift();
      var v = it[0], ring = it[1];
      if (ring > maxRing) maxRing = ring;
      for (var k = 0; k < nodes[v].children.length; k++) {
        q.push([nodes[v].children[k], ring + 1]);
      }
    }
    return maxRing * RING_STEP;
  }

  function expandedRadius(v) {
    if (!nodes[v].isRoot) return 0;
    if (!nodes[v].expanded) return Math.max(nodes[v].w, nodes[v].h) / 2;
    return starMaxRadius(v);
  }

  function placeRoots() {
    var n = roots.length;
    for (var i = 0; i < n; i++) {
      var r = roots[i];
      var n0 = nodes[r];
      n0.tx = PAD + (i + 0.5) * ((W - 2 * PAD) / n);
      n0.ty = H / 2;
    }
  }

  function separateRoots() {
    for (var it = 0; it < 30; it++) {
      var any = false;
      for (var i = 0; i < roots.length; i++) {
        for (var j = i + 1; j < roots.length; j++) {
          var a = nodes[roots[i]];
          var b = nodes[roots[j]];
          var ra = expandedRadius(roots[i]);
          var rb = expandedRadius(roots[j]);
          var minD = ra + rb + 40;
          var dx = b.tx - a.tx;
          if (Math.abs(dx) < minD) {
            any = true;
            var push = (minD - Math.abs(dx)) / 2;
            if (dx >= 0) { a.tx -= push; b.tx += push; }
            else { a.tx += push; b.tx -= push; }
          }
        }
      }
      if (!any) break;
    }
    for (var i = 0; i < roots.length; i++) {
      var n0 = nodes[roots[i]];
      var r = expandedRadius(roots[i]);
      var px = Math.max(r, n0.w / 2) + 4;
      if (n0.tx - px < PAD) n0.tx = PAD + px;
      if (n0.tx + px > W - PAD) n0.tx = W - PAD - px;
      if (n0.ty - r - n0.h / 2 < PAD) n0.ty = PAD + r + n0.h / 2;
      if (n0.ty + r + n0.h / 2 > H - PAD) n0.ty = H - PAD - r - n0.h / 2;
    }
  }

  function computeTargets() {
    placeRoots();
    separateRoots();

    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.isRoot) {
        n.tx = nodes[n._idx].tx;
        n.ty = nodes[n._idx].ty;
      }
    }

    for (var ri = 0; ri < roots.length; ri++) {
      var r = roots[ri];
      var root = nodes[r];
      if (!root.expanded) continue;
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (n === root) continue;
        if (rootOf(i) !== r) continue;
        var rr = n.ring * RING_STEP;
        n.tx = root.tx + Math.cos(n.angle) * rr;
        n.ty = root.ty + Math.sin(n.angle) * rr;
      }
    }

    for (var ri = 0; ri < roots.length; ri++) {
      var r = roots[ri];
      var root = nodes[r];
      if (root.expanded) continue;
      for (var i = 0; i < nodes.length; i++) {
        if (i === r) continue;
        if (rootOf(i) !== r) continue;
        nodes[i].tx = root.tx;
        nodes[i].ty = root.ty;
      }
    }
  }

  // ---- simulation ----

  function simulate() {
    var i, j, a, b, dx, dy, d, f, ux, uy;

    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.dragging) continue;
      var goalX = n.tx + n.userDx;
      var goalY = n.ty + n.userDy;
      var k = n.isRoot ? 0.14 : 0.10;
      n.vx += (goalX - n.x) * k;
      n.vy += (goalY - n.y) * k;
    }

    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      if (isHidden(a)) continue;
      var ra = Math.sqrt(a.w * a.w + a.h * a.h) / 2;
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        if (isHidden(b)) continue;
        dx = b.x - a.x;
        dy = b.y - a.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < 1) d2 = 1;
        var dist = Math.sqrt(d2);
        var rb = Math.sqrt(b.w * b.w + b.h * b.h) / 2;
        var minDist = ra + rb + 8;
        if (dist < minDist) {
          var overlap = minDist - dist;
          ux = dx / dist; uy = dy / dist;
          f = Math.min(overlap * 0.28, 5);
          if (!a.dragging) { a.vx -= ux * f; a.vy -= uy * f; }
          if (!b.dragging) { b.vx += ux * f; b.vy += uy * f; }
        }
      }
    }

    for (i = 0; i < edges.length; i++) {
      a = nodes[edges[i][0]];
      b = nodes[edges[i][1]];
      if (isHidden(a) && isHidden(b)) continue;
      dx = b.x - a.x;
      dy = b.y - a.y;
      d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      var rest = (a.h + b.h) / 2 + 30;
      f = (d - rest) * 0.006;
      ux = dx / d; uy = dy / d;
      if (!a.dragging) { a.vx += ux * f; a.vy += uy * f; }
      if (!b.dragging) { b.vx -= ux * f; b.vy -= uy * f; }
    }

    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.dragging) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.80;
      n.vy *= 0.80;
      var sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (sp > 14) { n.vx = n.vx / sp * 14; n.vy = n.vy / sp * 14; }
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  function isHidden(n) {
    if (n.isRoot) return false;
    var r = rootOf(n._idx);
    return !nodes[r].expanded;
  }

  // ---- rendering ----

  function roundRect(x, y, w, h, r) {
    var x0 = x - w / 2, y0 = y - h / 2;
    ctx.beginPath();
    ctx.moveTo(x0 + r, y0);
    ctx.arcTo(x0 + w, y0,     x0 + w, y0 + h, r);
    ctx.arcTo(x0 + w, y0 + h, x0,     y0 + h, r);
    ctx.arcTo(x0,     y0 + h, x0,     y0,     r);
    ctx.arcTo(x0,     y0,     x0 + w, y0,     r);
    ctx.closePath();
  }

  function render() {
    paintBackground();

    ctx.lineWidth = 1.6;
    ctx.lineCap = 'round';
    for (var i = 0; i < edges.length; i++) {
      var a = nodes[edges[i][0]];
      var b = nodes[edges[i][1]];
      if (isHidden(a) || isHidden(b)) continue;
      var stroke;
      if (a.isSection || b.isSection) {
        stroke = ROOT_STROKE + '80';
      } else {
        stroke = branchColor(b.rootIndex).stroke + '80';
      }
      ctx.strokeStyle = stroke;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (isHidden(n)) continue;
      var bc = branchColor(n.rootIndex);

      if (n.expanded) {
        ctx.shadowColor = 'rgba(255,207,94,0.6)';
        ctx.shadowBlur = 22;
      }

      roundRect(n.x, n.y, n.w, n.h, 8);

      if (n.expanded) {
        ctx.fillStyle = '#3a2e10';
        ctx.strokeStyle = '#ffcf5e';
        ctx.lineWidth = 2.2;
      } else if (n.isRoot) {
        ctx.fillStyle = ROOT_FILL;
        ctx.strokeStyle = ROOT_STROKE;
        ctx.lineWidth = 2.8;
      } else if (n.parent >= 0 && nodes[n.parent].isSection) {
        ctx.fillStyle = bc.fill;
        ctx.strokeStyle = bc.stroke;
        ctx.lineWidth = 2.4;
      } else {
        ctx.fillStyle = bc.fill;
        ctx.strokeStyle = bc.stroke + '99';
        ctx.lineWidth = 1.3;
      }
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.shadowColor = 'transparent';
      ctx.stroke();

      if (n.expanded) {
        ctx.fillStyle = '#fff8e0';
      } else if (n.isRoot) {
        ctx.fillStyle = ROOT_TEXT;
      } else if (n.parent >= 0 && nodes[n.parent].isSection) {
        ctx.fillStyle = bc.text;
      } else {
        ctx.fillStyle = bc.text + 'cc';
      }
      ctx.font = (n.isRoot ? '700 ' : '600 ') +
                 n.fontSize.toFixed(1) + 'px system-ui, sans-serif';
      ctx.fillText(n.text, n.x, n.y + 0.5);
    }
  }

  function animateSizes() {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var prev = n.fontSize;
      n.fontSize += (n.targetSize - n.fontSize) * 0.22;
      if (Math.abs(n.fontSize - prev) > 0.01) {
        measure(n, n.fontSize);
      }
    }
  }

  function loop() {
    simulate();
    simulate();
    animateSizes();
    render();
    requestAnimationFrame(loop);
  }

  function initLayout() {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      n.fontSize = FONT_BASE;
      n.targetSize = FONT_BASE;
      measure(n, n.fontSize);
    }
    computeTargets();
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      n.x = n.tx + n.userDx;
      n.y = n.ty + n.userDy;
      n.vx = 0;
      n.vy = 0;
    }
  }

  // ---- interaction ----
  var dragNode = null;
  var downX = 0, downY = 0, downT = 0;
  var dragged = false;

  function getPos(e) {
    var r = canvas.getBoundingClientRect();
    var p = e.touches ? e.touches[0] : e;
    return { x: p.clientX - r.left, y: p.clientY - r.top };
  }

  function pick(x, y) {
    for (var i = nodes.length - 1; i >= 0; i--) {
      var n = nodes[i];
      if (isHidden(n)) continue;
      if (Math.abs(x - n.x) <= n.w / 2 && Math.abs(y - n.y) <= n.h / 2) {
        return n;
      }
    }
    return null;
  }

  function toggleRoot(r) {
    r.expanded = !r.expanded;
    if (r.expanded) {
      r.targetSize = Math.min(FONT_MAX, FONT_BASE * FONT_BIG_FACTOR);
    } else {
      r.targetSize = FONT_BASE;
      for (var i = 0; i < nodes.length; i++) {
        if (i === r._idx) continue;
        if (rootOf(i) === r._idx) {
          nodes[i].userDx = 0;
          nodes[i].userDy = 0;
        }
      }
    }
    computeTargets();
  }

  function startDrag(p) {
    downX = p.x; downY = p.y; downT = Date.now();
    dragged = false;
    var n = pick(p.x, p.y);
    if (!n) { dragNode = null; return; }
    dragNode = n;
    n.dragging = true;
  }

  function moveDrag(p) {
    if (!dragNode) return;
    dragged = true;
    if (dragNode.isRoot) {
      dragNode.x = p.x;
      dragNode.y = p.y;
      dragNode.tx = p.x;
      dragNode.ty = p.y;
    } else {
      dragNode.x = p.x;
      dragNode.y = p.y;
      dragNode.userDx = p.x - dragNode.tx;
      dragNode.userDy = p.y - dragNode.ty;
    }
  }

  function endDrag() {
    if (dragNode) {
      dragNode.dragging = false;
      if (dragNode.isRoot) {
        separateRoots();
        computeTargets();
      }
    }
    if (!dragged && dragNode) {
      if (dragNode.isRoot) {
        toggleRoot(dragNode);
      } else {
        dragNode.expanded = !dragNode.expanded;
        dragNode.targetSize = dragNode.expanded
          ? Math.min(FONT_MAX, FONT_BASE * FONT_BIG_FACTOR)
          : FONT_BASE;
      }
    }
    dragNode = null;
  }

  canvas.addEventListener('mousedown', function (e) {
    startDrag(getPos(e));
  });

  window.addEventListener('mousemove', function (e) {
    if (!dragNode) return;
    moveDrag(getPos(e));
  });

  window.addEventListener('mouseup', function (e) {
    if (!dragNode) return;
    endDrag();
  });

  canvas.addEventListener('touchstart', function (e) {
    startDrag(getPos(e));
    e.preventDefault();
  }, { passive: false });

  canvas.addEventListener('touchmove', function (e) {
    if (!dragNode) return;
    moveDrag(getPos(e));
    e.preventDefault();
  }, { passive: false });

  canvas.addEventListener('touchend', function (e) {
    if (!dragNode) return;
    endDrag();
    e.preventDefault();
  }, { passive: false });

  var resizeTimer = null;
  window.addEventListener('resize', function () {
    resize();
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      computeTargets();
    }, 40);
  });

  resize();
  initLayout();
  loop();
})();
</script>
</body>
</html>
"""


def make_html(nodes, bg_data_uri):
    data = json.dumps(nodes, ensure_ascii=False, separators=(',', ':'))
    html = HTML_TEMPLATE.replace('__GRAPH_DATA__', data, 1)
    html = html.replace('__BG_IMAGE__', bg_data_uri, 1)
    return html


def main():
    # 1. Read or create tree.txt
    if not os.path.exists(TREE_FILE):
        os.makedirs(os.path.dirname(TREE_FILE), exist_ok=True)
        with open(TREE_FILE, 'w', encoding='utf-8') as f:
            f.write(SAMPLE)
        print('Created sample file:', TREE_FILE)
        text = SAMPLE
    else:
        with open(TREE_FILE, 'r', encoding='utf-8') as f:
            text = f.read()

    # 2. Parse
    entries = parse(text)
    print('Entries found:', len(entries))
    for num, label, section in entries[:12]:
        kind = 'section' if num is None else 'task'
        print('   [%s] %s -> %s (section: %s)' % (kind, num, label, section))

    if not entries:
        print('No tasks. Edit', TREE_FILE, 'and run again.')
        return

    # 3. Build nodes
    nodes = build_nodes(entries)
    print('Nodes built:', len(nodes))

    # 4. Embed background image
    bg_data_uri = embed_image(IMG_FILE)
    if bg_data_uri:
        print('Background image embedded:', IMG_FILE)
    else:
        print('Background image not found:', IMG_FILE)

    # 5. HTML
    html = make_html(nodes, bg_data_uri)
    path = os.path.abspath(HTML_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print('HTML saved:', path)

    # 6. Open
    webbrowser.open('file://' + path)


if __name__ == '__main__':
    main()