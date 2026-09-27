import os
import re
import json
import webbrowser

TREE_FILE = 'source/tree.txt'
HTML_FILE = 'tasks.html'

SAMPLE = """# Структура задач: одна задача — одна строка.
# Номер задаёт иерархию: 1 → 1.1 → 1.1.1
1. Поход
1.1 Купить вещи
1.2 Купить еду
2. Школа
3. Программирование
3.1 Заказать курсы
3.2 Купить контроллер
"""


# ---------- парсинг ----------

def parse(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        m = re.match(r'^(\d+(?:\.\d+)*)\.?\s+(.+)$', s)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def build_nodes(entries):
    nodes = []
    idx = {}
    for num, label in entries:
        if num in idx:
            continue
        idx[num] = len(nodes)
        nodes.append({
            'id': num,
            'label': label,
            'parent': -1,
            'children': [],
            'level': 0,
            'slot': 0,
            'nx': 0.5,
        })

    for i, n in enumerate(nodes):
        if '.' in n['id']:
            pnum = n['id'].rsplit('.', 1)[0]
            pi = idx.get(pnum, -1)
            if pi >= 0:
                n['parent'] = pi
                nodes[pi]['children'].append(i)

    roots = [i for i, n in enumerate(nodes) if n['parent'] == -1]
    if not roots:
        return nodes

    # уровни BFS
    for r in roots:
        nodes[r]['level'] = 0
    q = list(roots)
    while q:
        v = q.pop(0)
        for c in nodes[v]['children']:
            nodes[c]['level'] = nodes[v]['level'] + 1
            q.append(c)

    # слоты pre-order (гарантия отсутствия пересечений)
    counter = [0]

    def assign_slot(v):
        nodes[v]['slot'] = counter[0]
        counter[0] += 1
        for c in nodes[v]['children']:
            assign_slot(c)

    for r in roots:
        assign_slot(r)

    # tidy X
    xs = [0.0] * len(nodes)
    leaf = [0]

    def calc_x(v):
        ch = nodes[v]['children']
        if not ch:
            xs[v] = float(leaf[0])
            leaf[0] += 1
        else:
            for c in ch:
                calc_x(c)
            xs[v] = (xs[ch[0]] + xs[ch[-1]]) / 2.0

    for r in roots:
        calc_x(r)

    mx = max(xs) if xs else 1.0
    if mx < 1e-6:
        mx = 1.0
    for i, n in enumerate(nodes):
        n['nx'] = xs[i] / mx

    return nodes


# ---------- html ----------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Дерево задач</title>
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
  #reset {
    position: fixed; top: 12px; right: 12px;
    padding: 7px 14px; background: #21262d; color: #e6edf3;
    border: 1px solid #30363d; border-radius: 6px;
    cursor: pointer; font-size: 13px;
  }
  #reset:hover { background: #30363d; }
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
  <b>Клик по узлу</b> — увеличить надпись<br>
  <b>Тяни узел</b> — дерево перестраивается
</div>
<button id="reset">Сбросить</button>
<div id="err"></div>

<script>
// Показать любую ошибку на странице
window.onerror = function (msg, src, line) {
  var e = document.getElementById('err');
  e.style.display = 'block';
  e.textContent += 'JS Error: ' + msg + ' (line ' + line + ')\\n';
};

// ---- ДАННЫЕ ИЗ PYTHON ----
var DATA = __GRAPH_DATA__;
// --------------------------

(function () {
  var canvas = document.getElementById('c');
  var ctx = canvas.getContext('2d');
  var W = 800, H = 600, DPR = 1;

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
    e.textContent = 'Нет данных: массив DATA пуст.';
    return;
  }

  // ---- построение ----
  var nodes = [];
  for (var i = 0; i < DATA.length; i++) {
    var d = DATA[i];
    nodes.push({
      id: d.id,
      label: d.label,
      text: d.id + '. ' + d.label,
      parent: d.parent,
      children: d.children,
      level: d.level,
      slot: d.slot,
      nx: d.nx,
      x: 0, y: 0, vx: 0, vy: 0,
      w: 100, h: 32,
      fontSize: 12, targetSize: 12,
      expanded: false, fixed: false
    });
  }

  var edges = [];
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].parent >= 0) edges.push([nodes[i].parent, i]);
  }

  var maxLevel = 0;
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].level > maxLevel) maxLevel = nodes[i].level;
  }

  var TOP = 80;
  var SPACING = 110;

  function measure(n) {
    ctx.font = '600 ' + n.fontSize.toFixed(1) + 'px system-ui, sans-serif';
    var tw = ctx.measureText(n.text).width;
    n.w = Math.ceil(tw + n.fontSize * 1.6 + 14);
    n.h = Math.ceil(n.fontSize * 1.8 + 14);
  }

  function layout() {
    var margin = 100;
    var span = Math.max(W - 2 * margin, 200);
    var availY = H - TOP - 80;
    SPACING = Math.max(80, Math.min(140, availY / Math.max(maxLevel, 1)));

    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      n.fontSize = 12;
      n.targetSize = 12;
      n.expanded = false;
      n.fixed = false;
      n.vx = 0;
      n.vy = 0;
      measure(n);
      n.x = margin + n.nx * span;
      n.y = TOP + n.level * SPACING;
    }
    spread(30);
  }

  // Держит порядок узлов внутри уровня. Гарантирует отсутствие пересечений.
  function spread(passes) {
    var levels = {};
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (!levels[n.level]) levels[n.level] = [];
      levels[n.level].push(n);
    }
    for (var lv in levels) {
      var arr = levels[lv];
      arr.sort(function (a, b) { return a.slot - b.slot; });
      for (var p = 0; p < passes; p++) {
        for (var i = 1; i < arr.length; i++) {
          var a = arr[i - 1], b = arr[i];
          var minD = a.w / 2 + b.w / 2 + 24;
          var d = b.x - a.x;
          if (d < minD) {
            var over = minD - d;
            if (!a.fixed && !b.fixed) { a.x -= over / 2; b.x += over / 2; }
            else if (!b.fixed) b.x += over;
            else if (!a.fixed) a.x -= over;
          }
        }
        for (var i = arr.length - 2; i >= 0; i--) {
          var a = arr[i], b = arr[i + 1];
          var minD = a.w / 2 + b.w / 2 + 24;
          var d = b.x - a.x;
          if (d < minD) {
            var over = minD - d;
            if (!a.fixed && !b.fixed) { a.x -= over / 2; b.x += over / 2; }
            else if (!a.fixed) a.x -= over;
            else if (!b.fixed) b.x += over;
          }
        }
      }
    }
  }

  function simulate() {
    var i, j, a, b, dx, dy, d, f, ux, uy;

    // Y-пружина к своему уровню
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.fixed) continue;
      var ty = TOP + n.level * SPACING;
      n.vy += (ty - n.y) * 0.2;
    }

    // Пружины рёбер
    for (i = 0; i < edges.length; i++) {
      a = nodes[edges[i][0]];
      b = nodes[edges[i][1]];
      dx = b.x - a.x;
      dy = b.y - a.y;
      d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      f = (d - SPACING) * 0.01;
      ux = dx / d; uy = dy / d;
      if (!a.fixed) { a.vx += ux * f; a.vy += uy * f * 0.5; }
      if (!b.fixed) { b.vx -= ux * f; b.vy -= uy * f * 0.5; }
    }

    // Мягкое отталкивание
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        dx = b.x - a.x;
        dy = b.y - a.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < 4) d2 = 4;
        var dist = Math.sqrt(d2);
        f = 500 / d2;
        ux = dx / dist; uy = dy / dist;
        if (!a.fixed) { a.vx -= ux * f; a.vy -= uy * f * 0.2; }
        if (!b.fixed) { b.vx += ux * f; b.vy += uy * f * 0.2; }
      }
    }

    // Интеграция
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.fixed) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.8;
      n.vy *= 0.8;
      var sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (sp > 15) { n.vx = n.vx / sp * 15; n.vy = n.vy / sp * 15; }
      n.x += n.vx;
      n.y += n.vy;
    }

    spread(3);

    // Границы окна
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.fixed) continue;
      var px = n.w / 2 + 4;
      var py = n.h / 2 + 4;
      if (n.x < px)         { n.x = px;         n.vx =  Math.abs(n.vx) * 0.5; }
      if (n.x > W - px)     { n.x = W - px;     n.vx = -Math.abs(n.vx) * 0.5; }
      if (n.y < py)         { n.y = py;         n.vy = 0; }
      if (n.y > H - py)     { n.y = H - py;     n.vy = 0; }
    }
  }

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
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, W, H);

    // Рёбра
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = 'rgba(120,160,230,0.4)';
    ctx.lineCap = 'round';
    for (var i = 0; i < edges.length; i++) {
      var a = nodes[edges[i][0]];
      var b = nodes[edges[i][1]];
      var y1 = a.y + a.h / 2;
      var y2 = b.y - b.h / 2;
      var midY = (y1 + y2) / 2;
      ctx.beginPath();
      ctx.moveTo(a.x, y1);
      ctx.bezierCurveTo(a.x, midY, b.x, midY, b.x, y2);
      ctx.stroke();
    }

    // Узлы
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];

      if (n.expanded) {
        ctx.shadowColor = 'rgba(255,207,94,0.6)';
        ctx.shadowBlur = 20;
      }

      roundRect(n.x, n.y, n.w, n.h, 8);

      if (n.expanded) {
        ctx.fillStyle = '#3a2e10';
        ctx.strokeStyle = '#ffcf5e';
        ctx.lineWidth = 2;
      } else if (n.level === 0) {
        ctx.fillStyle = '#1a2547';
        ctx.strokeStyle = 'rgba(140,180,255,0.6)';
        ctx.lineWidth = 1.4;
      } else {
        ctx.fillStyle = '#131c33';
        ctx.strokeStyle = 'rgba(120,160,230,0.45)';
        ctx.lineWidth = 1.2;
      }
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.shadowColor = 'transparent';
      ctx.stroke();

      ctx.fillStyle = n.expanded ? '#fff8e0' : '#dbe4f5';
      ctx.font = '600 ' + n.fontSize.toFixed(1) + 'px system-ui, sans-serif';
      ctx.fillText(n.text, n.x, n.y + 0.5);
    }
  }

  function animateSizes() {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var prev = n.fontSize;
      n.fontSize += (n.targetSize - n.fontSize) * 0.22;
      if (Math.abs(n.fontSize - prev) > 0.01) measure(n);
    }
  }

  // ---- цикл ----
  function loop() {
    simulate();
    simulate();
    animateSizes();
    render();
    requestAnimationFrame(loop);
  }

  // ---- взаимодействие ----
  var dragNode = null, downX = 0, downY = 0, downT = 0;

  function getPos(e) {
    var r = canvas.getBoundingClientRect();
    var p = e.touches ? e.touches[0] : e;
    return { x: p.clientX - r.left, y: p.clientY - r.top };
  }

  function pick(x, y) {
    for (var i = nodes.length - 1; i >= 0; i--) {
      var n = nodes[i];
      if (Math.abs(x - n.x) <= n.w / 2 && Math.abs(y - n.y) <= n.h / 2) {
        return n;
      }
    }
    return null;
  }

  function toggle(n) {
    n.expanded = !n.expanded;
    n.targetSize = n.expanded ? 20 : 12;
  }

  canvas.addEventListener('mousedown', function (e) {
    var p = getPos(e);
    var n = pick(p.x, p.y);
    if (n) {
      dragNode = n;
      n.fixed = true;
      downX = p.x; downY = p.y; downT = Date.now();
      canvas.classList.add('drag');
    }
  });

  window.addEventListener('mousemove', function (e) {
    if (!dragNode) return;
    var p = getPos(e);
    dragNode.x = p.x;
    dragNode.y = p.y;
    dragNode.vx = 0;
    dragNode.vy = 0;
  });

  window.addEventListener('mouseup', function (e) {
    if (dragNode) {
      var p = getPos(e);
      var dx = p.x - downX, dy = p.y - downY;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 6 && Date.now() - downT < 400) toggle(dragNode);
      dragNode.fixed = false;
      dragNode = null;
      canvas.classList.remove('drag');
    }
  });

  // Touch
  canvas.addEventListener('touchstart', function (e) {
    var p = getPos(e);
    var n = pick(p.x, p.y);
    if (n) {
      dragNode = n;
      n.fixed = true;
      downX = p.x; downY = p.y; downT = Date.now();
      e.preventDefault();
    }
  }, { passive: false });

  canvas.addEventListener('touchmove', function (e) {
    if (!dragNode) return;
    var p = getPos(e);
    dragNode.x = p.x;
    dragNode.y = p.y;
    dragNode.vx = 0;
    dragNode.vy = 0;
    e.preventDefault();
  }, { passive: false });

  canvas.addEventListener('touchend', function (e) {
    if (dragNode) {
      var t = e.changedTouches[0];
      var r = canvas.getBoundingClientRect();
      var x = t.clientX - r.left, y = t.clientY - r.top;
      var dx = x - downX, dy = y - downY;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 8 && Date.now() - downT < 500) toggle(dragNode);
      dragNode.fixed = false;
      dragNode = null;
    }
  });

  document.getElementById('reset').addEventListener('click', function () {
    layout();
  });

  window.addEventListener('resize', function () {
    resize();
    layout();
  });

  resize();
  layout();
  loop();
})();
</script>
</body>
</html>
"""


def make_html(nodes):
    data = json.dumps(nodes, ensure_ascii=False, separators=(',', ':'))
    return HTML_TEMPLATE.replace('__GRAPH_DATA__', data, 1)


def main():
    # 1. Читаем или создаём tree.txt
    if not os.path.exists(TREE_FILE):
        with open(TREE_FILE, 'w', encoding='utf-8') as f:
            f.write(SAMPLE)
        print('Создан файл с примером:', TREE_FILE)
        text = SAMPLE
    else:
        with open(TREE_FILE, 'r', encoding='utf-8') as f:
            text = f.read()

    # 2. Парсим
    entries = parse(text)
    print('Строк-задач найдено:', len(entries))
    for num, label in entries[:10]:
        print('   ', num, '→', label)

    if not entries:
        print('Нет задач. Отредактируй', TREE_FILE, 'и запусти снова.')
        return

    # 3. Строим узлы
    nodes = build_nodes(entries)
    print('Узлов построено:', len(nodes))

    # 4. HTML
    html = make_html(nodes)
    path = os.path.abspath(HTML_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print('HTML сохранён:', path)

    # 5. Открываем
    webbrowser.open('file://' + path)


if __name__ == '__main__':
    main()
