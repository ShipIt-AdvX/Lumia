'use strict';
/* 浮窗共用标题栏: 全屏切换、关闭, 以及上报悬停以驱动自动收起/召回. */
(function () {
  const fsBtn = document.getElementById('tbFs');
  const closeBtn = document.getElementById('tbClose');
  const titleEl = document.getElementById('tbTitle');

  if (titleEl && window.lumia) titleEl.textContent = window.lumia.title || 'Lumia';

  if (fsBtn) fsBtn.addEventListener('click', () => window.lumia.win.toggleFullscreen());
  if (closeBtn) closeBtn.addEventListener('click', () => window.lumia.win.close());

  window.lumia.win.onFullscreenChanged((isFull) => {
    document.body.classList.toggle('is-fullscreen', isFull);
    if (fsBtn) fsBtn.textContent = isFull ? '退出全屏' : '全屏';
    if (isFull) document.body.classList.remove('is-retracted');
  });

  // 上报悬停, 主进程据此做 10 秒自动收起与召回
  const root = document.documentElement;
  root.addEventListener('mouseenter', () => {
    document.body.classList.remove('is-retracted');
    window.lumia.win.setHover(true);
  });
  root.addEventListener('mouseleave', () => window.lumia.win.setHover(false));

  // 主进程告知已收起, 我们据此显示把手
  window.lumia.win.onRetractedChanged((retracted) =>
    document.body.classList.toggle('is-retracted', retracted));

  // 启动即开始收起倒计时 (鼠标移入会取消)
  window.lumia.win.setHover(false);
})();
