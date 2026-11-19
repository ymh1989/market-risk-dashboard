(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const icons = JSON.parse($("icons").textContent);
  const scenes = {
    water: { title: "산과 물 사이", kicker: "WATER / STILLNESS", alt: "산과 숲으로 둘러싸인 청록빛 물가" },
    forest: { title: "숲이 깨어나는 시간", kicker: "FOREST / EARLY LIGHT", alt: "안개가 천천히 흐르는 푸른 숲" },
    alps: { title: "알프스의 능선", kicker: "ALPINE / OPEN SKY", alt: "알프스의 산과 능선 위로 흐르는 구름" },
  };
  const order = Object.keys(scenes);
  const videos = [$("film-a"), $("film-b")];
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let active = -1, sceneId = "water", paused = motion.matches, loading = false;
  let generation = 0, controller = null, fade = null, raf = 0, lastFrame = 0;
  let idleTimer = 0, noticeTimer = 0, clockTimer = 0, sceneTime = 0;
  let look = { x: 0, y: 0 }, target = { x: 0, y: 0 };
  const asset = (id, ext) => `./src/media/nature/${id}.${ext}`;

  function showNotice(text) {
    clearTimeout(noticeTimer);
    $("notice").textContent = text;
    noticeTimer = setTimeout(() => { $("notice").textContent = ""; }, 4500);
  }

  function wake() {
    document.body.classList.remove("idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (!paused && !loading && $("picker").hidden && !document.querySelector(".chrome :focus-visible")) {
        document.body.classList.add("idle");
      }
    }, 5500);
  }

  function updateClock() {
    clearTimeout(clockTimer);
    const now = new Date();
    $("clock").textContent = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now);
    $("clock").dateTime = now.toISOString();
    $("clock").title = "한국시간";
    $("date").textContent = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "long", day: "numeric", weekday: "short" }).format(now);
    if (!document.hidden) clockTimer = setTimeout(updateClock, 30000);
  }

  function syncControls() {
    const label = paused ? "재생" : "일시정지";
    $("pause").setAttribute("aria-label", label);
    $("pause").title = label;
    $("pause").setAttribute("aria-pressed", String(paused));
    $("pause").innerHTML = icons[paused ? "play" : "pause"];
    document.body.dataset.paused = String(paused);
  }

  function showScene(id) {
    sceneId = id;
    $("scene-title").textContent = scenes[id].title;
    $("scene-kicker").textContent = scenes[id].kicker;
    $("poster").src = asset(id, "jpg");
    $("poster").alt = scenes[id].alt;
    document.querySelectorAll("button[data-scene]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.scene === id));
    });
    document.body.dataset.scene = id;
  }

  function settleFade() {
    if (!fade) return;
    active = fade.to;
    videos[active].style.opacity = "1";
    videos[fade.from].style.opacity = "0";
    videos[fade.from].pause();
    fade = null;
    document.body.dataset.transition = "false";
  }

  function waitForFilm(video, signal) {
    return new Promise((resolve, reject) => {
      let timeout;
      // 되감기에서는 loadeddata가 다시 오지 않을 수 있으므로 탐색 완료도 확인합니다.
      const readinessEvents = ["loadeddata", "canplay", "seeked"];
      const isReady = () => video.readyState >= 2 && !video.seeking;
      const cleanup = () => {
        clearTimeout(timeout);
        readinessEvents.forEach((name) => video.removeEventListener(name, ready));
        video.removeEventListener("error", failed);
        signal.removeEventListener("abort", aborted);
      };
      const ready = () => { if (isReady()) { cleanup(); resolve(); } };
      const failed = () => { cleanup(); reject(new Error("영상 불러오기 실패")); };
      const aborted = () => { cleanup(); reject(new DOMException("장면 변경", "AbortError")); };
      readinessEvents.forEach((name) => video.addEventListener(name, ready));
      video.addEventListener("error", failed);
      signal.addEventListener("abort", aborted, { once: true });
      timeout = setTimeout(() => { if (!video.error && isReady()) ready(); else failed(); }, 15000);
      if (signal.aborted) aborted();
      else if (video.error) failed();
      else ready();
    });
  }

  async function playFilm(video) {
    if (paused || document.hidden) return;
    try {
      await video.play();
      // 비동기 재생 요청 도중 탭을 벗어나거나 일시정지했을 수 있습니다.
      if (paused || document.hidden) video.pause();
    } catch (error) {
      if (error.name === "AbortError") return;
      paused = true;
      videos.forEach((v) => v.pause());
      syncControls();
      wake();
      showNotice("재생 버튼으로 풍경을 시작할 수 있습니다.");
    }
  }

  async function loadScene(id, loop = false) {
    if (!scenes[id]) return;
    const ticket = ++generation;
    controller?.abort();
    controller = new AbortController();
    settleFade();
    loading = true;
    document.body.dataset.loading = "true";
    $("retry").hidden = true;
    const next = active < 0 ? 0 : 1 - active;
    const video = videos[next];
    video.pause();
    video.style.opacity = "0";
    video.preload = "auto";
    video.muted = true;
    video.playbackRate = Number($("speed").value);
    if (video.dataset.scene !== id || video.error) {
      video.dataset.scene = id;
      video.src = asset(id, "mp4");
      video.load();
    } else {
      video.currentTime = 0;
    }
    try {
      await waitForFilm(video, controller.signal);
      if (ticket !== generation) return;
      await playFilm(video);
      if (ticket !== generation) { video.pause(); return; }
      if (active < 0 || paused || motion.matches) {
        videos.forEach((v) => { if (v !== video) { v.pause(); v.style.opacity = "0"; } });
        active = next;
        video.style.opacity = "1";
      } else {
        fade = { from: active, to: next, elapsed: 0, duration: loop ? 1.6 : 2.2 };
        document.body.dataset.transition = "true";
      }
      if (!loop) { showScene(id); sceneTime = 0; }
      $("status").hidden = true;
      document.body.dataset.ready = "true";
      loading = false;
      document.body.dataset.loading = "false";
      requestFrame();
      if (!loop) wake();
    } catch (error) {
      if (ticket !== generation || error.name === "AbortError") return;
      loading = false;
      document.body.dataset.loading = "false";
      paused = true;
      videos.forEach((v) => v.pause());
      syncControls();
      $("status").textContent = "영상을 불러오지 못했습니다. 다른 풍경을 선택하거나 다시 시도해 주세요.";
      $("status").hidden = false;
      $("retry").hidden = false;
      $("retry").onclick = () => { paused = motion.matches; syncControls(); loadScene(id); };
      wake();
    }
  }

  function requestFrame() {
    if (!raf && !document.hidden) raf = requestAnimationFrame(frame);
  }

  function frame(now) {
    raf = 0;
    if (document.hidden) return;
    const dt = Math.min((now - (lastFrame || now)) / 1000, .08);
    lastFrame = now;
    const moving = Math.abs(target.x - look.x) + Math.abs(target.y - look.y) > .02;
    if (moving && !motion.matches && !paused) {
      const ease = 1 - Math.exp(-dt * 2.6);
      look.x += (target.x - look.x) * ease;
      look.y += (target.y - look.y) * ease;
      $("landscape").style.setProperty("--look-x", `${look.x.toFixed(2)}px`);
      $("landscape").style.setProperty("--look-y", `${look.y.toFixed(2)}px`);
    }
    if (!paused && active >= 0) {
      sceneTime += dt;
      if (fade) {
        fade.elapsed += dt;
        const t = Math.min(1, fade.elapsed / fade.duration);
        const eased = t * t * (3 - 2 * t);
        // 아래 영상을 불투명하게 유지해 교차 전환 중 검은 화면이 생기지 않게 합니다.
        videos[fade.from].style.zIndex = "1";
        videos[fade.to].style.zIndex = "2";
        videos[fade.to].style.opacity = String(eased);
        if (t >= 1) settleFade();
      } else if (!loading) {
        const film = videos[active];
        if (Number.isFinite(film.duration) && film.duration > 0) {
          $("progress").style.transform = `scaleX(${Math.min(1, film.currentTime / film.duration)})`;
          const remaining = (film.duration - film.currentTime) / film.playbackRate;
          if (remaining < 2.1) {
            const tour = $("tour").checked && sceneTime > 55;
            const next = tour ? order[(order.indexOf(sceneId) + 1) % order.length] : sceneId;
            loadScene(next, !tour);
          }
        }
      }
      requestFrame();
    }
  }

  function setPaused(value) {
    paused = value;
    syncControls();
    if (paused) {
      videos.forEach((v) => v.pause());
      cancelAnimationFrame(raf); raf = 0;
    } else {
      const indices = fade ? [fade.from, fade.to] : active >= 0 ? [active] : [];
      indices.forEach((i) => playFilm(videos[i]));
      lastFrame = performance.now();
      requestFrame();
    }
    wake();
  }

  function setPicker(open, focus = false) {
    $("picker").hidden = !open;
    $("scenes").setAttribute("aria-expanded", String(open));
    if (focus) (open ? document.querySelector(`button[data-scene="${sceneId}"]`) : $("scenes")).focus();
    wake();
  }
  $("scenes").onclick = () => setPicker($("picker").hidden, true);
  $("close-picker").onclick = () => setPicker(false, true);
  document.querySelectorAll("button[data-scene]").forEach((button) => {
    button.onclick = () => { setPicker(false, true); loadScene(button.dataset.scene); };
  });
  $("pause").onclick = () => setPaused(!paused);
  $("speed").onchange = () => { videos.forEach((v) => { v.playbackRate = Number($("speed").value); }); wake(); };
  $("tour").onchange = wake;
  $("fullscreen").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
      else showNotice("이 브라우저에서는 전체 화면을 지원하지 않습니다.");
    } catch { showNotice("전체 화면으로 전환하지 못했습니다."); }
    wake();
  };
  document.addEventListener("fullscreenchange", () => {
    const label = document.fullscreenElement ? "전체 화면 종료" : "전체 화면";
    $("fullscreen").setAttribute("aria-label", label); $("fullscreen").title = label;
  });
  document.addEventListener("pointermove", (event) => {
    wake();
    if (event.pointerType !== "mouse" || motion.matches || paused) return;
    target = { x: (event.clientX / innerWidth - .5) * -16, y: (event.clientY / innerHeight - .5) * -10 };
    requestFrame();
  }, { passive: true });
  document.addEventListener("pointerdown", (event) => {
    wake();
    if (!event.target.closest("#picker, #scenes")) setPicker(false);
  }, { passive: true });
  document.addEventListener("keydown", (event) => {
    wake();
    if (event.key === "Escape" && !$("picker").hidden) setPicker(false, true);
    if (event.code === "Space" && !event.target.closest("button, a, select, input")) {
      event.preventDefault(); setPaused(!paused);
    }
  });
  document.addEventListener("focusin", wake);
  // 숨겨진 탭에서는 영상 디코딩과 화면 갱신을 모두 멈춥니다.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      videos.forEach((v) => v.pause());
      cancelAnimationFrame(raf); raf = 0;
      clearTimeout(clockTimer); clearTimeout(idleTimer);
    } else {
      updateClock();
      setPaused(paused);
    }
  });
  motion.addEventListener("change", () => { if (motion.matches) setPaused(true); });
  addEventListener("pagehide", () => {
    videos.forEach((v) => v.pause());
    cancelAnimationFrame(raf); raf = 0;
    clearTimeout(clockTimer); clearTimeout(idleTimer);
  });
  addEventListener("pageshow", (event) => { if (event.persisted) { updateClock(); setPaused(paused); } });
  updateClock(); syncControls(); loadScene("water"); wake();
})();
