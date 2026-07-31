#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PortalCommand:
    label: str
    command: str
    description: str


@dataclass(frozen=True)
class PortalLink:
    label: str
    href: str
    description: str


@dataclass(frozen=True)
class PortalEnvironment:
    label: str
    value: str


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render_commands(commands: Iterable[PortalCommand]) -> str:
    cards = []
    for index, item in enumerate(commands, start=1):
        command = _escape(item.command)
        cards.append(
            f"""
            <article class="command-card">
              <div class="command-heading">
                <span class="step">{index}</span>
                <div>
                  <h3>{_escape(item.label)}</h3>
                  <p>{_escape(item.description)}</p>
                </div>
              </div>
              <div class="command-row">
                <code>{command}</code>
                <button type="button" data-copy={json.dumps(item.command)}>Copy</button>
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def _render_links(links: Iterable[PortalLink]) -> str:
    return "\n".join(
        f"""
        <a class="resource-card" href="{_escape(item.href)}">
          <strong>{_escape(item.label)}</strong>
          <span>{_escape(item.description)}</span>
        </a>
        """
        for item in links
    )


def _render_environment(items: Iterable[PortalEnvironment]) -> str:
    return "\n".join(
        f"""
        <div class="environment-row">
          <dt>{_escape(item.label)}</dt>
          <dd>{_escape(item.value)}</dd>
        </div>
        """
        for item in items
    )


def _render_topology(items: Iterable[str]) -> str:
    nodes = []
    values = list(items)
    for index, item in enumerate(values):
        nodes.append(f'<div class="topology-node">{_escape(item)}</div>')
        if index < len(values) - 1:
            nodes.append('<div class="topology-arrow" aria-hidden="true">→</div>')
    return "\n".join(nodes)


def _render_notes(items: Iterable[str]) -> str:
    return "\n".join(f"<li>{_escape(item)}</li>" for item in items)


def write_recipe_portal(
    output: Path,
    *,
    recipe_id: str,
    title: str,
    summary: str,
    commands: Iterable[PortalCommand],
    links: Iterable[PortalLink],
    environment: Iterable[PortalEnvironment],
    topology: Iterable[str],
    agency_notes: Iterable[str],
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
    }}
    body {{
      margin: 0;
      background: Canvas;
      color: CanvasText;
    }}
    main {{
      width: min(1080px, calc(100% - 32px));
      margin: 0 auto;
      padding: 48px 0 72px;
    }}
    header, section {{ margin-bottom: 36px; }}
    .eyebrow {{
      margin: 0 0 8px;
      font-size: 0.85rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      opacity: 0.7;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 5vw, 3.4rem);
      line-height: 1.15;
    }}
    .summary {{ max-width: 760px; font-size: 1.1rem; }}
    .status {{
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 6px 12px;
      border: 1px solid color-mix(in srgb, CanvasText 24%, transparent);
      border-radius: 999px;
      font-weight: 700;
    }}
    .status::before {{
      content: "";
      width: 0.7rem;
      height: 0.7rem;
      border-radius: 50%;
      background: #2ca44f;
    }}
    .topology {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }}
    .topology-node, .command-card, .resource-card, .panel {{
      border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
      border-radius: 14px;
      background: color-mix(in srgb, Canvas 96%, CanvasText 4%);
    }}
    .topology-node {{ padding: 12px 16px; font-weight: 700; }}
    .topology-arrow {{ font-size: 1.5rem; opacity: 0.65; }}
    .commands {{ display: grid; gap: 16px; }}
    .command-card {{ padding: 18px; }}
    .command-heading {{
      display: flex;
      gap: 14px;
      align-items: flex-start;
    }}
    .command-heading h3, .command-heading p {{ margin: 0; }}
    .step {{
      display: grid;
      place-items: center;
      min-width: 2rem;
      height: 2rem;
      border-radius: 50%;
      background: CanvasText;
      color: Canvas;
      font-weight: 800;
    }}
    .command-row {{
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 14px;
    }}
    code {{
      flex: 1;
      overflow-x: auto;
      padding: 12px;
      border-radius: 10px;
      background: color-mix(in srgb, CanvasText 10%, Canvas);
      white-space: nowrap;
    }}
    button {{
      padding: 10px 14px;
      border: 1px solid color-mix(in srgb, CanvasText 28%, transparent);
      border-radius: 10px;
      background: Canvas;
      color: CanvasText;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
    }}
    .resource-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }}
    .resource-card {{
      display: grid;
      gap: 4px;
      padding: 16px;
      color: inherit;
      text-decoration: none;
    }}
    .resource-card:hover {{ border-color: CanvasText; }}
    .resource-card span {{ opacity: 0.72; }}
    .panel {{ padding: 18px; }}
    dl {{ margin: 0; }}
    .environment-row {{
      display: grid;
      grid-template-columns: minmax(150px, 220px) 1fr;
      gap: 16px;
      padding: 8px 0;
      border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
    }}
    .environment-row:last-child {{ border-bottom: 0; }}
    dt {{ font-weight: 700; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    .warning {{ border-left: 5px solid #d29922; }}
    footer {{ opacity: 0.65; font-size: 0.9rem; }}
    @media (max-width: 640px) {{
      .command-row {{ align-items: stretch; flex-direction: column; }}
      .environment-row {{ grid-template-columns: 1fr; gap: 0; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <p class="eyebrow">Hakoniwa Business Pack · Generated Recipe Workspace</p>
    <h1>{_escape(title)}</h1>
    <p class="summary">{_escape(summary)}</p>
    <p class="status">Foundation ready</p>
  </header>

  <section>
    <h2>Runtime topology</h2>
    <div class="topology">{_render_topology(topology)}</div>
  </section>

  <section>
    <h2>Operator workflow</h2>
    <div class="commands">{_render_commands(commands)}</div>
  </section>

  <section>
    <h2>Resources</h2>
    <div class="resource-grid">{_render_links(links)}</div>
  </section>

  <section>
    <h2>Resolved environment</h2>
    <div class="panel"><dl>{_render_environment(environment)}</dl></div>
  </section>

  <section>
    <h2>Agency boundary</h2>
    <div class="panel warning"><ul>{_render_notes(agency_notes)}</ul></div>
  </section>

  <footer>
    Recipe ID: <code>{_escape(recipe_id)}</code>. This page documents the generated
    workspace; it does not execute local commands from the browser.
  </footer>
</main>
<script>
  document.querySelectorAll("[data-copy]").forEach((button) => {{
    button.addEventListener("click", async () => {{
      const command = button.dataset.copy;
      try {{
        await navigator.clipboard.writeText(command);
        const original = button.textContent;
        button.textContent = "Copied";
        window.setTimeout(() => {{ button.textContent = original; }}, 1200);
      }} catch (_error) {{
        window.prompt("Copy this command:", command);
      }}
    }});
  }});
</script>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")
    return output
