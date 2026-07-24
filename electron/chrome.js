'use strict';
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
  });
})();
