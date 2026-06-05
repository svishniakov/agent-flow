<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentFlow README</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #2d2d2b;
      --panel: #343431;
      --panel-2: #3b3a36;
      --line: rgba(249, 249, 247, 0.14);
      --line-soft: rgba(249, 249, 247, 0.08);
      --text: #f9f9f7;
      --muted: #c7c1b8;
      --faint: #928c82;
      --accent: #cc7d5e;
      --accent-soft: rgba(204, 125, 94, 0.16);
      --accent-line: rgba(204, 125, 94, 0.36);
      --good: #00c853;
      --bad: #ff5f38;
      --max: 1180px;
    }

    * {
      box-sizing: border-box;
    }

    html {
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 16px;
      letter-spacing: 0;
    }

    body {
      margin: 0;
      min-width: 320px;
      background:
        linear-gradient(180deg, rgba(249, 249, 247, 0.035), transparent 22rem),
        radial-gradient(circle at 50% 0%, rgba(204, 125, 94, 0.14), transparent 22rem),
        var(--bg);
    }

    a {
      color: inherit;
    }

    h1,
    h2,
    h3,
    p {
      margin: 0;
    }

    .page {
      overflow: hidden;
    }

    .shell {
      width: min(100% - 2rem, var(--max));
      margin: 0 auto;
    }

    .hero {
      padding: 2rem 0 4rem;
    }

    .nav {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      min-height: 3.25rem;
      margin-bottom: 2.5rem;
    }

    .brand {
      display: inline-flex;
      align-items: center;
      gap: 0.7rem;
      color: var(--text);
      text-decoration: none;
      font-weight: 750;
    }

    .mark {
      width: 2.2rem;
      height: 2.2rem;
      border-radius: 0.75rem;
      display: grid;
      place-items: center;
      background: var(--accent-soft);
      border: 1px solid var(--line);
      color: var(--text);
      box-shadow: inset 0 1px 0 rgba(249, 249, 247, 0.1);
    }

    .mark svg {
      width: 1.25rem;
      height: 1.25rem;
    }

    .nav-links {
      display: none;
      gap: 0.5rem;
      color: var(--muted);
      font-size: 0.9rem;
    }

    .nav-links a {
      min-height: 2.75rem;
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 0 0.9rem;
      text-decoration: none;
    }

    .nav-links a:hover {
      background: rgba(249, 249, 247, 0.06);
      color: var(--text);
    }

    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      min-height: 2rem;
      padding: 0 0.8rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(249, 249, 247, 0.04);
      font-size: 0.75rem;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: 0.14em;
    }

    .dot {
      width: 0.45rem;
      height: 0.45rem;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 0 0.25rem var(--accent-soft);
    }

    h1 {
      margin-top: 1.2rem;
      max-width: 13ch;
      margin-left: auto;
      margin-right: auto;
      font-size: 3rem;
      line-height: 0.98;
      letter-spacing: 0;
      text-align: center;
      text-wrap: balance;
    }

    .hero-copy {
      margin-top: 1.2rem;
      max-width: 50rem;
      margin-left: auto;
      margin-right: auto;
      color: var(--muted);
      font-size: 1.05rem;
      line-height: 1.55;
      text-align: center;
    }

    .actions,
    .badges {
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
    }

    .actions {
      gap: 0.8rem;
      margin-top: 1.7rem;
    }

    .button {
      min-height: 3rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.7rem;
      border-radius: 999px;
      padding: 0.25rem 0.3rem 0.25rem 1.1rem;
      border: 1px solid var(--line);
      text-decoration: none;
      color: var(--text);
      font-weight: 750;
      background: rgba(249, 249, 247, 0.055);
      transition: transform 500ms cubic-bezier(0.32, 0.72, 0, 1), background 500ms cubic-bezier(0.32, 0.72, 0, 1);
    }

    .button.primary {
      background: var(--accent);
      border: 0;
    }

    .button:hover {
      transform: translateY(-2px);
      background: rgba(249, 249, 247, 0.08);
    }

    .button.primary:hover {
      background: #d38b70;
    }

    .button span {
      width: 2.35rem;
      height: 2.35rem;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: rgba(8, 10, 15, 0.12);
    }

    .badges {
      gap: 0.5rem;
      margin-top: 1.3rem;
    }

    .badge {
      min-height: 2rem;
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line-soft);
      border-radius: 0.55rem;
      padding: 0 0.7rem;
      color: var(--muted);
      background: rgba(249, 249, 247, 0.04);
      font-size: 0.78rem;
      font-weight: 650;
    }

    .hero-card {
      border-radius: 1.6rem;
      padding: 0.45rem;
      width: min(100%, 980px);
      margin: 2.4rem auto 0;
      background: rgba(249, 249, 247, 0.055);
      border: 1px solid var(--line);
      box-shadow:
        0 1.8rem 5rem rgba(0, 0, 0, 0.32),
        inset 0 1px 0 rgba(249, 249, 247, 0.12);
    }

    .diagram {
      --dot-travel: 30rem;
      min-height: auto;
      border-radius: 1.2rem;
      background:
        linear-gradient(180deg, rgba(249, 249, 247, 0.055), rgba(249, 249, 247, 0.02)),
        var(--panel);
      border: 1px solid var(--line-soft);
      padding: 1rem;
      position: relative;
      overflow: hidden;
    }

    .diagram::before {
      content: "";
      position: absolute;
      inset: 0;
      background-image:
        linear-gradient(rgba(249, 249, 247, 0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(249, 249, 247, 0.035) 1px, transparent 1px);
      background-size: 2rem 2rem;
      mask-image: linear-gradient(180deg, black, transparent 80%);
      pointer-events: none;
      z-index: 0;
    }

    .diagram-inner {
      position: relative;
      display: grid;
      gap: 0.9rem;
      z-index: 2;
    }

    .node {
      min-height: 4.2rem;
      border: 1px solid var(--line);
      border-radius: 1rem;
      padding: 0.9rem;
      background: rgba(45, 45, 43, 0.78);
      box-shadow: inset 0 1px 0 rgba(249, 249, 247, 0.08);
    }

    .node small {
      display: block;
      color: var(--faint);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-weight: 800;
    }

    .node strong {
      display: block;
      margin-top: 0.25rem;
      color: var(--text);
      font-size: 1rem;
    }

    .node p {
      margin-top: 0.35rem;
      color: var(--muted);
      font-size: 0.85rem;
      line-height: 1.45;
    }

    .node.gate,
    .node.done {
      border-color: var(--accent-line);
    }

    .node.work {
      border-color: rgba(0, 200, 83, 0.3);
    }

    .node.locked {
      border-color: rgba(255, 95, 56, 0.32);
    }

    .connector {
      display: none;
      position: absolute;
      height: 2px;
      background: linear-gradient(90deg, transparent, var(--accent), transparent);
      z-index: 1;
      opacity: 0.38;
    }

    .flow-dots {
      position: absolute;
      inset: 0;
      z-index: 3;
      pointer-events: none;
      overflow: hidden;
    }

    .flow-dot {
      position: absolute;
      left: -1rem;
      top: var(--y);
      width: 0.55rem;
      height: 0.55rem;
      border-radius: 50%;
      background: rgba(204, 125, 94, 0.55);
      opacity: 0;
      box-shadow:
        0 0 0 0.2rem rgba(204, 125, 94, 0.07),
        0 0 0.9rem rgba(204, 125, 94, 0.34);
      animation: process-dot var(--duration) linear infinite;
      animation-delay: var(--delay);
    }

    .flow-dot.vertical {
      top: -1rem;
      left: var(--x);
      animation-name: process-dot-vertical;
    }

    .flow-dot::before {
      content: "";
      position: absolute;
      top: 50%;
      right: 0.2rem;
      width: 2.8rem;
      height: 1px;
      transform: translateY(-50%);
      background: linear-gradient(90deg, transparent, rgba(204, 125, 94, 0.24));
    }

    @keyframes process-dot {
      0% {
        opacity: 0;
        transform: translate3d(0, 0, 0) scale(0.8);
      }
      8%,
      88% {
        opacity: 1;
      }
      100% {
        opacity: 0;
        transform: translate3d(var(--dot-travel), var(--drift, 0), 0) scale(1);
      }
    }

    @keyframes process-dot-vertical {
      0% {
        opacity: 0;
        transform: translate3d(0, 0, 0) scale(0.8);
      }
      8%,
      88% {
        opacity: 1;
      }
      100% {
        opacity: 0;
        transform: translate3d(var(--drift, 0), 20rem, 0) scale(1);
      }
    }

    section {
      padding: 3.8rem 0;
    }

    .section-head {
      display: grid;
      gap: 0.7rem;
      max-width: 44rem;
      margin: 0 auto 1.5rem;
      text-align: center;
    }

    .section-head h2 {
      font-size: 2rem;
      line-height: 1;
      letter-spacing: 0;
      text-wrap: balance;
    }

    .section-head p {
      color: var(--muted);
      line-height: 1.6;
      font-size: 1.03rem;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 1rem;
    }

    .card {
      border-radius: 1.2rem;
      padding: 1rem;
      background: var(--panel);
      border: 1px solid var(--line-soft);
      box-shadow: inset 0 1px 0 rgba(249, 249, 247, 0.08);
    }

    .card h3 {
      font-size: 1.05rem;
      line-height: 1.25;
    }

    .card p {
      margin-top: 0.55rem;
      color: var(--muted);
      line-height: 1.55;
      font-size: 0.95rem;
    }

    .card .num {
      width: 2.2rem;
      height: 2.2rem;
      display: grid;
      place-items: center;
      border-radius: 0.7rem;
      margin-bottom: 1rem;
      color: var(--text);
      background: var(--accent);
      font-weight: 800;
    }

    .role-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 0.8rem;
    }

    .role {
      display: grid;
      gap: 0.35rem;
      min-height: 6.4rem;
      border-radius: 1rem;
      padding: 0.95rem;
      background: rgba(45, 45, 43, 0.68);
      border: 1px solid var(--line-soft);
    }

    .role.hot {
      border-color: var(--accent-line);
    }

    .role small {
      color: var(--faint);
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    .role strong {
      font-size: 1.05rem;
    }

    .role span {
      color: var(--muted);
      font-size: 0.84rem;
    }

    .code-shell {
      position: relative;
      border-radius: 1.3rem;
      padding: 0.45rem;
      width: min(100%, 900px);
      margin-left: auto;
      margin-right: auto;
      background: rgba(249, 249, 247, 0.055);
      border: 1px solid var(--line);
      min-width: 0;
      overflow: hidden;
    }

    .copy-button {
      position: absolute;
      top: 0.75rem;
      right: 0.75rem;
      z-index: 2;
      min-width: 4.6rem;
      min-height: 2.3rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0 0.8rem;
      color: var(--text);
      background: rgba(45, 45, 43, 0.92);
      box-shadow: inset 0 1px 0 rgba(249, 249, 247, 0.1);
      font: inherit;
      font-size: 0.78rem;
      font-weight: 800;
      cursor: pointer;
      transition: transform 400ms cubic-bezier(0.32, 0.72, 0, 1), border-color 400ms cubic-bezier(0.32, 0.72, 0, 1);
    }

    .copy-button:hover {
      transform: translateY(-1px);
      border-color: var(--accent-line);
    }

    .copy-button:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }

    pre {
      margin: 0;
      max-width: 100%;
      overflow-x: auto;
      border-radius: 0.95rem;
      padding: 1rem;
      background: #242421;
      color: var(--text);
      font-size: 0.88rem;
      line-height: 1.65;
      border: 1px solid var(--line-soft);
    }

    [data-copy-block] pre {
      padding-top: 3rem;
    }

    .terminal-title {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      margin-bottom: 0.75rem;
      color: var(--faint);
      font-size: 0.8rem;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }

    .lights {
      display: inline-flex;
      gap: 0.3rem;
    }

    .lights i {
      width: 0.58rem;
      height: 0.58rem;
      border-radius: 50%;
      background: var(--bad);
    }

    .lights i:nth-child(2) {
      background: var(--accent);
    }

    .lights i:nth-child(3) {
      background: var(--good);
    }

    .footer {
      padding: 2.5rem 0 3.5rem;
      color: var(--faint);
      border-top: 1px solid var(--line-soft);
    }

    @media (min-width: 768px) {
      .nav-links {
        display: flex;
      }

      .hero {
        padding-top: 2.4rem;
        padding-bottom: 5.5rem;
      }

      h1 {
        font-size: 5rem;
      }

      .hero-copy {
        font-size: 1.18rem;
      }

      .diagram {
        --dot-travel: 68rem;
        padding: 1.2rem;
      }

      @keyframes process-dot-vertical {
        0% {
          opacity: 0;
          transform: translate3d(0, 0, 0) scale(0.8);
        }
        8%,
        88% {
          opacity: 1;
        }
        100% {
          opacity: 0;
          transform: translate3d(var(--drift, 0), 15rem, 0) scale(1);
        }
      }

      .diagram-inner {
        grid-template-columns: repeat(5, minmax(0, 1fr));
        align-items: stretch;
      }

      .connector {
        display: block;
      }

      .connector.a {
        left: 18%;
        right: 18%;
        top: 50%;
      }

      .grid.three {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      .grid.four {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .role-grid {
        grid-template-columns: repeat(5, minmax(0, 1fr));
      }

      section {
        padding: 4.7rem 0;
      }

      .section-head h2 {
        font-size: 3.2rem;
      }
    }

    @media (min-width: 1024px) {
      .shell {
        width: min(100% - 3rem, var(--max));
      }

      h1 {
        font-size: 6rem;
      }

      .grid.four {
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }

      .card {
        padding: 1.25rem;
      }
    }

  </style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <div class="shell">
        <nav class="nav" aria-label="README navigation">
          <a class="brand" href="#">
            <span class="mark" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M4 12h6m0 0 2-4m-2 4 2 4m2-8h6m-6 8h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </span>
            AgentFlow
          </a>
          <div class="nav-links">
            <a href="#contract">Контракт</a>
            <a href="#inside">Внутри</a>
            <a href="#install">Установка</a>
            <a href="README.en.md">EN</a>
          </div>
        </nav>

        <div class="hero-grid">
          <div>
            <span class="eyebrow"><span class="dot"></span> Codex skill</span>
            <h1>Оркестратор для агентов.</h1>
            <p class="hero-copy">
              AgentFlow даёт агенту общую память, роли, model/reasoning config, role skills и проверку результата. Субагенты запускаются только после отдельного разрешения.
            </p>
            <div class="actions" aria-label="Primary actions">
              <a class="button primary" href="#install">Установить <span>↓</span></a>
              <a class="button" href="#prompts">Промпты <span>→</span></a>
            </div>
            <div class="badges">
              <span class="badge">25 role files</span>
              <span class="badge">138 role skills</span>
              <span class="badge">.agent-work/tasks</span>
              <span class="badge">Apache 2.0</span>
            </div>
          </div>

          <div class="hero-card" aria-label="AgentFlow process">
            <div class="diagram">
              <div class="connector a"></div>
              <div class="flow-dots" aria-hidden="true">
                <i class="flow-dot" style="--y: 50%; --duration: 7.2s; --delay: -0.4s;"></i>
                <i class="flow-dot" style="--y: 27%; --duration: 8.8s; --delay: -2.2s; --drift: 0.4rem;"></i>
                <i class="flow-dot" style="--y: 72%; --duration: 6.8s; --delay: -3.8s; --drift: -0.4rem;"></i>
                <i class="flow-dot" style="--y: 39%; --duration: 9.4s; --delay: -5.4s; --drift: 0.2rem;"></i>
                <i class="flow-dot vertical" style="--x: 24%; --duration: 6.2s; --delay: -1.1s; --drift: 0.25rem;"></i>
                <i class="flow-dot vertical" style="--x: 52%; --duration: 7.6s; --delay: -3.4s; --drift: -0.35rem;"></i>
                <i class="flow-dot vertical" style="--x: 78%; --duration: 6.9s; --delay: -4.9s; --drift: 0.2rem;"></i>
              </div>
              <div class="diagram-inner">
                <article class="node gate">
                  <small>prefix</small>
                  <strong>Agent Flow</strong>
                  <p>Явный запуск skill.</p>
                </article>
                <article class="node">
                  <small>memory</small>
                  <strong>.agent-work</strong>
                  <p>Todo, lessons, notes.</p>
                </article>
                <article class="node work">
                  <small>roles</small>
                  <strong>25 agents</strong>
                  <p>Модель, reasoning, skills.</p>
                </article>
                <article class="node locked">
                  <small>gate</small>
                  <strong>Subagents</strong>
                  <p>Только по явному запросу.</p>
                </article>
                <article class="node done">
                  <small>done</small>
                  <strong>Verified</strong>
                  <p>Проверка перед ответом.</p>
                </article>
              </div>
            </div>
          </div>
        </div>
      </div>
    </header>

    <section id="contract">
      <div class="shell">
        <div class="section-head">
          <span class="eyebrow"><span class="dot"></span> Contract</span>
          <h2>Только явный запуск.</h2>
          <p>AgentFlow не является preflight и не включается автоматически. Префикс не разрешает субагентов.</p>
        </div>
        <div class="grid three">
          <article class="card">
            <div class="num">1</div>
            <h3>Invocation prefix</h3>
            <p><code>Agent Flow</code>, <code>$agent-flow</code> или <code>agent-flow</code> в начале запроса.</p>
          </article>
          <article class="card">
            <div class="num">2</div>
            <h3>Solo default</h3>
            <p>Короткие задачи идут solo. Оркестратор всё равно держит scope и проверку.</p>
          </article>
          <article class="card">
            <div class="num">3</div>
            <h3>Subagent gate</h3>
            <p>Делегирование только по отдельной просьбе: <code>use subagents</code>, <code>spawn a subagent</code>, <code>multi-agent review</code>.</p>
          </article>
        </div>
      </div>
    </section>

    <section id="inside">
      <div class="shell">
        <div class="section-head">
          <span class="eyebrow"><span class="dot"></span> Inside</span>
          <h2>Память, роли, config.</h2>
          <p>Всё важное для маршрута лежит в repo: role files, registry, references, scripts.</p>
        </div>
        <div class="grid four">
          <article class="card">
            <h3>Общая память</h3>
            <p><code>.agent-work/tasks/</code>: todo, lessons, implementation notes, verification, handoff.</p>
          </article>
          <article class="card">
            <h3>Оркестратор</h3>
            <p>Главный агент выбирает маршрут, назначает роли, собирает handoff, проверяет результат.</p>
          </article>
          <article class="card">
            <h3>Role config</h3>
            <p><code>model</code>, <code>reasoning_effort</code>, <code>escalation_triggers</code> на роль.</p>
          </article>
          <article class="card">
            <h3>Skills registry</h3>
            <p><code>registries/agent-skills.json</code> хранит install metadata для role skills.</p>
          </article>
        </div>
      </div>
    </section>

    <section id="roles">
      <div class="shell">
        <div class="section-head">
          <span class="eyebrow"><span class="dot"></span> Agents</span>
          <h2>25 узких ролей.</h2>
          <p>Каждая роль имеет ownership, allowed tools, model config, skills и ожидаемый handoff.</p>
        </div>
        <div class="role-grid">
          <article class="role hot"><small>planning</small><strong>architect</strong><span>gpt-5.5 · high</span></article>
          <article class="role"><small>review</small><strong>reviewer</strong><span>risk · regression</span></article>
          <article class="role hot"><small>verify</small><strong>qa-verifier</strong><span>checks · evidence</span></article>
          <article class="role"><small>source</small><strong>researcher</strong><span>facts · browser</span></article>
          <article class="role hot"><small>design</small><strong>visual-qa</strong><span>screens · UI</span></article>
          <article class="role"><small>web</small><strong>frontend-worker</strong><span>UI · browser</span></article>
          <article class="role hot"><small>api</small><strong>backend-worker</strong><span>DB · queues</span></article>
          <article class="role"><small>ts</small><strong>typescript-worker</strong><span>types · tests</span></article>
          <article class="role hot"><small>py</small><strong>python-worker</strong><span>scripts · tooling</span></article>
          <article class="role"><small>apple</small><strong>ios-worker</strong><span>SwiftUI · sim</span></article>
        </div>
      </div>
    </section>

    <section id="install">
      <div class="shell">
        <div class="section-head">
          <span class="eyebrow"><span class="dot"></span> Install</span>
          <h2>Две команды.</h2>
          <p><code>--post-install</code> показывает missing skills и рекомендует <code>core</code>. Silent install нет.</p>
        </div>
        <div class="code-shell" data-copy-block>
          <button class="copy-button" type="button">Copy</button>
          <pre><code>git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install</code></pre>
        </div>
      </div>
    </section>

    <section id="checks">
      <div class="shell">
        <div class="section-head">
          <span class="eyebrow"><span class="dot"></span> Checks</span>
          <h2>Проверка окружения.</h2>
        </div>
        <div class="grid three">
          <div class="code-shell" data-copy-block>
            <button class="copy-button" type="button">Copy</button>
            <div class="terminal-title"><span class="lights"><i></i><i></i><i></i></span> deps</div>
            <pre><code>python3 scripts/check-agent-deps.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/check-agent-deps.py --scope role:typescript-worker
python3 scripts/check-agent-deps.py --strict</code></pre>
          </div>
          <div class="code-shell" data-copy-block>
            <button class="copy-button" type="button">Copy</button>
            <div class="terminal-title"><span class="lights"><i></i><i></i><i></i></span> install plan</div>
            <pre><code>python3 scripts/check-agent-deps.py --scope core --install-plan
python3 scripts/check-agent-deps.py --scope full --install-plan --target project
python3 scripts/check-agent-deps.py --scope core --guided-install</code></pre>
          </div>
          <div class="code-shell" data-copy-block>
            <button class="copy-button" type="button">Copy</button>
            <div class="terminal-title"><span class="lights"><i></i><i></i><i></i></span> repo</div>
            <pre><code>python3 -m py_compile scripts/*.py
python3 scripts/validate-agent-config.py
python3 scripts/validate-agent-skill-registry.py
python3 scripts/validate-run.py --help</code></pre>
          </div>
        </div>
      </div>
    </section>

    <section id="prompts">
      <div class="shell">
        <div class="section-head">
          <span class="eyebrow"><span class="dot"></span> Prompts</span>
          <h2>Короткие запросы.</h2>
        </div>
        <div class="grid three">
          <div class="code-shell" data-copy-block>
            <button class="copy-button" type="button">Copy</button>
            <div class="terminal-title"><span class="lights"><i></i><i></i><i></i></span> solo</div>
            <pre><code>Agent Flow Прочитай репозиторий, память проекта и README. Верни active, blocked, next actions, risks. Ничего не меняй.</code></pre>
          </div>
          <div class="code-shell" data-copy-block>
            <button class="copy-button" type="button">Copy</button>
            <div class="terminal-title"><span class="lights"><i></i><i></i><i></i></span> bugfix</div>
            <pre><code>Agent Flow Разбери баг: &lt;описание&gt;. Найди причину, исправь минимально, запусти проверки, верни changed files и risks.</code></pre>
          </div>
          <div class="code-shell" data-copy-block>
            <button class="copy-button" type="button">Copy</button>
            <div class="terminal-title"><span class="lights"><i></i><i></i><i></i></span> subagents</div>
            <pre><code>Agent Flow Используй субагентов для независимого review. Раздели работу по ролям и сведи findings в один итог.</code></pre>
          </div>
        </div>
      </div>
    </section>

    <footer class="footer">
      <div class="shell">
        Apache 2.0 · <a href="LICENSE">LICENSE</a>
      </div>
    </footer>
  </main>

  <script>
    const copyButtons = document.querySelectorAll("[data-copy-block] .copy-button");

    copyButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        const block = button.closest("[data-copy-block]");
        const code = block?.querySelector("code")?.textContent ?? "";

        try {
          await navigator.clipboard.writeText(code);
          button.textContent = "Copied";
        } catch {
          const textarea = document.createElement("textarea");
          textarea.value = code;
          textarea.setAttribute("readonly", "");
          textarea.style.position = "fixed";
          textarea.style.opacity = "0";
          document.body.appendChild(textarea);
          textarea.select();
          document.execCommand("copy");
          textarea.remove();
          button.textContent = "Copied";
        }

        window.setTimeout(() => {
          button.textContent = "Copy";
        }, 1300);
      });
    });
  </script>
</body>
</html>
