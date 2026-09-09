/*
 * api-quota 状态条：在 ZCode 输入框下方显示 API 余额与速度。
 * 由 patch-zcode.py 注入 app.asar（out/renderer/quota-status.js + index.html 引用）。
 * 数据来自本机 http://127.0.0.1:8788/quota（quota-server.py）。
 */
(function () {
  'use strict';

  var ENDPOINT = 'http://127.0.0.1:8788/quota';
  var REFRESH_MS = 30000;
  var ENSURE_MS = 2000;
  var NODE_ID = 'zcode-api-quota-status';
  var REGION_SELECTOR = '.chat-composer-region';

  var state = { text: '', tone: 'muted' };

  function colorFor(tone) {
    if (tone === 'warn') return 'var(--color-warning, #f0a04b)';
    if (tone === 'bad') return 'var(--color-danger, #f06b6b)';
    return 'var(--color-foreground-subtle, #8b8b8b)';
  }

  function ensureNode() {
    var host = document.querySelector(REGION_SELECTOR);
    if (!host) return null;
    var node = document.getElementById(NODE_ID);
    if (node && node.parentNode === host) return node;
    if (node && node.parentNode) node.parentNode.removeChild(node);
    node = document.createElement('div');
    node.id = NODE_ID;
    node.setAttribute('data-api-quota', '1');
    node.style.cssText = [
      'display:flex',
      'justify-content:flex-end',
      'align-items:center',
      'gap:12px',
      'width:100%',
      'padding:3px 8px 1px',
      'font-size:11px',
      'line-height:16px',
      'font-variant-numeric:tabular-nums',
      'white-space:nowrap',
      'overflow:hidden',
      'user-select:none',
      'pointer-events:none'
    ].join(';');
    node.textContent = state.text;
    node.style.color = colorFor(state.tone);
    host.appendChild(node);
    return node;
  }

  function render(text, tone) {
    state.text = text;
    state.tone = tone || 'muted';
    var node = ensureNode();
    if (node) {
      node.textContent = state.text;
      node.style.color = colorFor(state.tone);
    }
  }

  function fmtRate(value) {
    if (value === null || value === undefined || !isFinite(value)) return '--';
    return Math.round(value).toLocaleString('en-US') + ' tok/s';
  }

  function balanceText(balance) {
    if (!balance.ok) return '余额 ' + (balance.message || '不可用');

    if (balance.kind === 'quota') {
      var bits = [];
      var wins = balance.windows || [];
      for (var i = 0; i < wins.length && bits.length < 3; i++) {
        var win = wins[i];
        if (win.remaining_pct !== null && win.remaining_pct !== undefined) {
          bits.push(win.label + ' ' + Math.round(win.remaining_pct) + '%');
        }
      }
      return '额度 ' + (bits.length ? bits.join(' · ') : '未知');
    }

    var list = balance.currencies || [];
    return list.length
      ? '余额 ' + list[0].currency + ' ' + list[0].total
      : '余额 ' + (balance.message || '不可用');
  }

  function describe(snap) {
    var parts = [];
    parts.push(balanceText(snap.balance || {}));

    var speed = snap.speed || {};
    var decode = speed.median_decode_rate;
    parts.push('速度 ' + fmtRate(decode === null || decode === undefined
      ? speed.median_rate : decode));
    if (speed.median_ttft_ms !== null && speed.median_ttft_ms !== undefined) {
      parts.push('首字 ' + (speed.median_ttft_ms / 1000).toFixed(1) + 's');
    }
    if (speed.cache_hit_rate !== null && speed.cache_hit_rate !== undefined) {
      parts.push('缓存 ' + Math.round(speed.cache_hit_rate * 100) + '%');
    }
    var stamp = String(snap.generated_at || '').slice(11, 16);
    if (stamp) parts.push(stamp + ' 更新');
    return parts.join('  ·  ');
  }

  function refresh() {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', ENDPOINT + '?t=' + Date.now(), true);
    xhr.timeout = 15000;
    xhr.onload = function () {
      try {
        var snap = JSON.parse(xhr.responseText);
        if (snap && snap.error) {
          render('余额查询出错：' + snap.error, 'bad');
          return;
        }
        render(describe(snap), 'muted');
      } catch (err) {
        render('余额数据解析失败', 'bad');
      }
    };
    xhr.onerror = xhr.ontimeout = function () {
      render('余额服务未启动（运行 scripts\\quota-server.py）', 'warn');
    };
    try {
      xhr.send();
    } catch (err) {
      render('余额服务未启动', 'warn');
    }
  }

  function boot() {
    ensureNode();
    refresh();
    // React 重渲染可能移除注入节点，定期补回
    setInterval(ensureNode, ENSURE_MS);
    setInterval(refresh, REFRESH_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
