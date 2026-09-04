/* nanobot 中文源码教学站 —— 导航生成 / 代码高亮 / 练习交互 */

(function () {
  "use strict";

  // ===========================================================================
  // 1. 章节目录（单一数据源，侧边栏与上下页导航都从这里生成）
  // ===========================================================================
  const CHAPTERS = [
    { file: "index.html", num: "00", title: "开始之前", group: "入门",
      desc: "学习路线、环境准备与阅读方式" },
    { file: "01-architecture.html", num: "01", title: "架构总览与数据流", group: "入门",
      desc: "一条消息从渠道到回复的完整旅程" },
    { file: "02-bus.html", num: "02", title: "消息总线与事件模型", group: "核心链路",
      desc: "MessageBus、InboundMessage、OutboundMessage" },
    { file: "03-loop.html", num: "03", title: "AgentLoop：七阶段流水线", group: "核心链路",
      desc: "调度、会话锁、注入队列与回合装配" },
    { file: "04-runner.html", num: "04", title: "AgentRunner：ReAct 循环", group: "核心链路",
      desc: "迭代、工具执行、并发分批与终止条件" },
    { file: "05-providers.html", num: "05", title: "Provider 抽象层", group: "能力层",
      desc: "统一消息格式、流式回调、重试与错误映射" },
    { file: "06-tools.html", num: "06", title: "工具系统", group: "能力层",
      desc: "Tool 协议、自动发现、Schema 与校验管线" },
    { file: "07-memory.html", num: "07", title: "记忆与上下文压缩", group: "能力层",
      desc: "JSONL 持久化、原子写入、Dream 两阶段整合" },
    { file: "08-channels.html", num: "08", title: "渠道适配层", group: "边缘层",
      desc: "BaseChannel、manifest 懒加载、消息规范化" },
    { file: "09-config.html", num: "09", title: "配置与命令路由", group: "边缘层",
      desc: "Pydantic camelCase、三层路由表、内置命令" },
    { file: "10-security.html", num: "10", title: "安全边界", group: "边缘层",
      desc: "工作区围栏、SSRF 防护、沙箱与升级策略" },
    { file: "11-context.html", num: "11", title: "系统提示与 Skills", group: "运行时",
      desc: "ContextBuilder、bootstrap 文件、渐进式技能加载" },
    { file: "12-gateway.html", num: "12", title: "Gateway 启动与定时任务", group: "运行时",
      desc: "进程装配、配置热更新、cron Dream、心跳" },
    { file: "13-subagent.html", num: "13", title: "子 Agent", group: "运行时",
      desc: "spawn 工具、隔离工具集、结果回注入主会话" },
    { file: "14-delivery.html", num: "14", title: "流式投递与回合生命周期", group: "运行时",
      desc: "TurnDelivery、stream_id、进度事件、ContextGovernor" },
    { file: "15-pairing.html", num: "15", title: "配对鉴权", group: "接入",
      desc: "allowFrom、配对码、fail-closed、/pairing" },
    { file: "16-api.html", num: "16", title: "OpenAI 兼容 HTTP API", group: "接入",
      desc: "process_direct、SSE、Bearer、会话锁" },
    { file: "17-exercises.html", num: "17", title: "综合练习与实战", group: "总结",
      desc: "跨章节问答、动手任务与调试路线" },
  ];

  const STORE_DONE = "nanobot_cn_done_v1";
  const STORE_THEME = "nanobot_cn_theme_v1";

  function currentFile() {
    const parts = location.pathname.split("/");
    const last = parts[parts.length - 1];
    return last === "" ? "index.html" : last;
  }

  function readDone() {
    try {
      return JSON.parse(localStorage.getItem(STORE_DONE) || "{}");
    } catch (_) {
      return {};
    }
  }

  function writeDone(map) {
    try {
      localStorage.setItem(STORE_DONE, JSON.stringify(map));
    } catch (_) {
      /* localStorage 不可用时静默降级 */
    }
  }

  // ===========================================================================
  // 2. 主题
  // ===========================================================================
  function initTheme() {
    let saved = null;
    try {
      saved = localStorage.getItem(STORE_THEME);
    } catch (_) {}
    const prefersDark =
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = saved || (prefersDark ? "dark" : "light");
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORE_THEME, next);
    } catch (_) {}
    syncThemeLabel();
  }

  function syncThemeLabel() {
    const btn = document.getElementById("theme-btn");
    if (btn) {
      btn.textContent = document.documentElement.dataset.theme === "dark" ? "☀ 亮色" : "☾ 暗色";
    }
  }

  // ===========================================================================
  // 3. 侧边栏 + 顶栏 + 上下页
  // ===========================================================================
  function buildSidebar() {
    const host = document.getElementById("sidebar");
    if (!host) return;
    const here = currentFile();
    const done = readDone();

    function linkHtml(ch) {
      const cls = [ch.file === here ? "current" : "", done[ch.file] ? "done" : ""]
        .filter(Boolean)
        .join(" ");
      return (
        '<a href="' + ch.file + '" class="' + cls + '">' +
        '<span class="num">' + ch.num + "</span><span>" + ch.title + "</span></a>"
      );
    }

    let mainHtml = "<nav>";
    let endHtml = '<nav class="nav-end">';
    let group = null;
    CHAPTERS.forEach((ch) => {
      const bucket = ch.group === "总结" ? "end" : "main";
      if (ch.group !== group) {
        group = ch.group;
        const title = '<div class="nav-group-title">' + group + "</div>";
        if (bucket === "end") endHtml += title;
        else mainHtml += title;
      }
      if (bucket === "end") endHtml += linkHtml(ch);
      else mainHtml += linkHtml(ch);
    });
    mainHtml += "</nav>";
    endHtml += "</nav>";

    host.innerHTML =
      '<a class="brand" href="index.html"><strong>nanobot 源码精读</strong>' +
      "<span>中文教学 · 源码 + 讲解 + 练习</span></a>" +
      mainHtml +
      endHtml;

    const current = host.querySelector("a.current");
    const scroller = current && current.closest("nav");
    if (current && scroller && scroller.scrollHeight > scroller.clientHeight) {
      const cRect = current.getBoundingClientRect();
      const sRect = scroller.getBoundingClientRect();
      scroller.scrollTop += cRect.top - sRect.top - scroller.clientHeight / 3;
    }
  }

  function buildTopbar() {
    const host = document.getElementById("topbar");
    if (!host) return;
    const here = currentFile();
    const ch = CHAPTERS.find((c) => c.file === here);
    host.innerHTML =
      '<button id="menu-btn" type="button" aria-label="目录">☰</button>' +
      '<div class="crumb">nanobot 源码精读 / <b>' +
      (ch ? ch.num + " " + ch.title : "") +
      "</b></div>" +
      '<button id="theme-btn" type="button">☾ 暗色</button>';

    document.getElementById("menu-btn").addEventListener("click", () => {
      document.body.classList.toggle("nav-open");
    });
    document.getElementById("theme-btn").addEventListener("click", toggleTheme);
    document.body.addEventListener("click", (e) => {
      if (
        document.body.classList.contains("nav-open") &&
        !e.target.closest("#sidebar") &&
        !e.target.closest("#menu-btn")
      ) {
        document.body.classList.remove("nav-open");
      }
    });
    syncThemeLabel();
  }

  function buildPager() {
    const host = document.getElementById("pager");
    if (!host) return;
    const here = currentFile();
    const i = CHAPTERS.findIndex((c) => c.file === here);
    if (i < 0) return;
    const prev = CHAPTERS[i - 1];
    const next = CHAPTERS[i + 1];
    host.className = "pager";
    host.innerHTML =
      (prev
        ? '<a href="' + prev.file + '"><span>← 上一章</span>' + prev.num + " " + prev.title + "</a>"
        : '<a class="ghost">—</a>') +
      (next
        ? '<a class="next" href="' + next.file + '"><span>下一章 →</span>' + next.num + " " + next.title + "</a>"
        : '<a class="ghost">—</a>');
  }

  function buildDoneToggle() {
    const host = document.getElementById("done-toggle");
    if (!host) return;
    const here = currentFile();
    const done = readDone();
    host.className = "done-toggle";
    host.innerHTML =
      '<input type="checkbox" id="done-cb"' + (done[here] ? " checked" : "") + ">" +
      '<label for="done-cb">我已读完并理解本章（进度保存在浏览器本地，用于侧边栏打勾）</label>';
    document.getElementById("done-cb").addEventListener("change", (e) => {
      const map = readDone();
      if (e.target.checked) map[here] = 1;
      else delete map[here];
      writeDone(map);
      buildSidebar();
    });
  }

  // ===========================================================================
  // 4. Python 语法高亮（离线、零依赖的简易分词器）
  // ===========================================================================
  const KEYWORDS = new Set([
    "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
    "del", "elif", "else", "except", "finally", "for", "from", "global", "if",
    "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
    "return", "try", "while", "with", "yield", "True", "False", "None",
  ]);
  const SOFT = new Set(["self", "cls"]);

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function highlightPython(src) {
    let out = "";
    let i = 0;
    const n = src.length;

    while (i < n) {
      const c = src[i];

      // 注释
      if (c === "#") {
        let j = src.indexOf("\n", i);
        if (j < 0) j = n;
        out += '<span class="tok-com">' + esc(src.slice(i, j)) + "</span>";
        i = j;
        continue;
      }

      // 字符串（含三引号与 f/r/b 前缀）
      const strMatch = /^(?:[rbuf]{0,2})?("""|'''|"|')/i.exec(src.slice(i, i + 6));
      if (strMatch && /["']/.test(strMatch[1][0])) {
        const prefixLen = strMatch[0].length - strMatch[1].length;
        const quote = strMatch[1];
        let j = i + prefixLen + quote.length;
        while (j < n) {
          if (src[j] === "\\") {
            j += 2;
            continue;
          }
          if (src.startsWith(quote, j)) {
            j += quote.length;
            break;
          }
          j += 1;
        }
        out += '<span class="tok-str">' + esc(src.slice(i, Math.min(j, n))) + "</span>";
        i = Math.min(j, n);
        continue;
      }

      // 装饰器
      if (c === "@" && (i === 0 || /[\n\s]/.test(src[i - 1]))) {
        const m = /^@[\w.]*/.exec(src.slice(i));
        if (m) {
          out += '<span class="tok-dec">' + esc(m[0]) + "</span>";
          i += m[0].length;
          continue;
        }
      }

      // 数字
      if (/[0-9]/.test(c) && !/[\w]/.test(src[i - 1] || "")) {
        const m = /^[0-9][0-9_.eExXbBoOaAcCdDfF]*/.exec(src.slice(i));
        out += '<span class="tok-num">' + esc(m[0]) + "</span>";
        i += m[0].length;
        continue;
      }

      // 标识符
      if (/[A-Za-z_]/.test(c)) {
        const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i));
        const w = m[0];
        const after = src.slice(i + w.length);
        const before = src.slice(0, i);

        if (KEYWORDS.has(w)) {
          out += '<span class="tok-kw">' + w + "</span>";
        } else if (SOFT.has(w)) {
          out += '<span class="tok-self">' + w + "</span>";
        } else if (/^\s*(class)\s+$/.test(before.slice(-12))) {
          out += '<span class="tok-cls">' + w + "</span>";
        } else if (/^\(/.test(after)) {
          out += '<span class="tok-fn">' + w + "</span>";
        } else if (/^[A-Z][A-Za-z0-9_]*$/.test(w)) {
          out += '<span class="tok-cls">' + w + "</span>";
        } else {
          out += w;
        }
        i += w.length;
        continue;
      }

      out += esc(c);
      i += 1;
    }
    return out;
  }

  function renderCodeBlocks() {
    document.querySelectorAll(".code").forEach((block) => {
      const codeEl = block.querySelector("pre code");
      if (!codeEl) return;

      // 去掉 HTML 缩进带来的公共前导空白
      let raw = codeEl.textContent.replace(/^\n/, "").replace(/\s+$/, "");
      const lines = raw.split("\n");
      const indents = lines
        .filter((l) => l.trim().length)
        .map((l) => l.match(/^ */)[0].length);
      const strip = indents.length ? Math.min.apply(null, indents) : 0;
      raw = lines.map((l) => l.slice(strip)).join("\n");

      const lang = block.dataset.lang || "python";
      codeEl.innerHTML = lang === "python" ? highlightPython(raw) : esc(raw);

      // 行号
      const start = parseInt(block.dataset.start || "0", 10);
      if (start > 0) {
        block.classList.add("numbered");
        const count = raw.split("\n").length;
        const nums = [];
        for (let k = 0; k < count; k++) nums.push(start + k);
        const gutter = document.createElement("div");
        gutter.className = "gutter";
        gutter.setAttribute("aria-hidden", "true");
        gutter.textContent = nums.join("\n");
        codeEl.parentElement.insertBefore(gutter, codeEl);
      }

      // 头部（文件路径 + 行号范围 + 复制）
      const path = block.dataset.file;
      if (path) {
        const head = document.createElement("div");
        head.className = "code-head";
        const count = raw.split("\n").length;
        const range = start > 0 ? "L" + start + "–L" + (start + count - 1) : "";
        head.innerHTML =
          '<span class="path"><b>' + esc(path) + "</b></span>" +
          '<span class="lines">' + range + "</span>" +
          '<button type="button">复制</button>';
        block.insertBefore(head, block.firstChild);
        head.querySelector("button").addEventListener("click", (e) => {
          navigator.clipboard.writeText(raw).then(
            () => {
              e.target.textContent = "已复制";
              setTimeout(() => (e.target.textContent = "复制"), 1400);
            },
            () => (e.target.textContent = "复制失败")
          );
        });
      }
    });
  }

  // ===========================================================================
  // 5. 练习卡片
  // ===========================================================================
  function initQuizzes() {
    document.querySelectorAll(".quiz").forEach((quiz, idx) => {
      const ans = quiz.querySelector(".ans");
      if (!ans) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "reveal";
      btn.textContent = "查看答案与解析";
      quiz.insertBefore(btn, ans);
      btn.addEventListener("click", () => {
        quiz.classList.toggle("open");
        btn.textContent = quiz.classList.contains("open") ? "收起答案" : "查看答案与解析";
      });
      quiz.dataset.qi = String(idx + 1);
    });
  }

  // ===========================================================================
  // 6. 阅读进度条 + 页内目录
  // ===========================================================================
  function initProgressBar() {
    const bar = document.getElementById("progress-bar");
    if (!bar) return;
    const update = () => {
      const h = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = (h > 0 ? (window.scrollY / h) * 100 : 0) + "%";
    };
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  }

  function buildToc() {
    const host = document.getElementById("toc");
    if (!host) return;
    const heads = document.querySelectorAll(".content h2");
    if (!heads.length) return;
    let html = '<div class="t">本章目录</div><ol>';
    heads.forEach((h, i) => {
      if (!h.id) h.id = "sec-" + (i + 1);
      html += '<li><a href="#' + h.id + '">' + h.textContent + "</a></li>";
    });
    const summaries = Array.from(document.querySelectorAll(".content .callout > .t"))
      .filter((el) => el.textContent.replace(/\s/g, "") === "本章小结");
    if (summaries.length) {
      const box = summaries[summaries.length - 1].closest(".callout");
      if (box && !box.id) box.id = "chapter-summary";
      html += '<li><a href="#chapter-summary">本章小结</a></li>';
    }
    host.className = "toc";
    host.innerHTML = html + "</ol>";
  }

  // ===========================================================================
  // 启动
  // ===========================================================================
  initTheme();
  document.addEventListener("DOMContentLoaded", () => {
    buildSidebar();
    buildTopbar();
    buildPager();
    buildDoneToggle();
    renderCodeBlocks();
    initQuizzes();
    buildToc();
    initProgressBar();
  });
})();
