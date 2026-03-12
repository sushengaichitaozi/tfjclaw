from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - import depends on runtime
    sync_playwright = None
    PLAYWRIGHT_IMPORT_ERROR = str(exc)

from .config import AgentConfig


DOM_SNAPSHOT_JS = """
(maxElements) => {
  const attrName = 'data-desktop-agent-id';
  const isVisible = (el) => {
    if (!el || !(el instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.right < 0) return false;
    if (rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
    return true;
  };

  const selectorOf = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
      let part = current.tagName.toLowerCase();
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((node) => node.tagName === current.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
        }
      }
      parts.unshift(part);
      current = current.parentElement;
      if (current && current.id) {
        parts.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
    }
    return parts.join(' > ');
  };

  const textOf = (el) => {
    const value = el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    return value.replace(/\\s+/g, ' ').trim();
  };

  const candidates = Array.from(
    document.querySelectorAll(
      'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"],[tabindex]'
    )
  ).filter(isVisible).slice(0, maxElements);

  const elements = candidates.map((el, index) => {
    const agentId = `dom-${index + 1}`;
    el.setAttribute(attrName, agentId);
    const rect = el.getBoundingClientRect();
    return {
      agent_id: agentId,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').slice(0, 40),
      text: textOf(el).slice(0, 160),
      aria_label: (el.getAttribute('aria-label') || '').slice(0, 120),
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 120),
      name: (el.getAttribute('name') || '').slice(0, 120),
      href: (el.getAttribute('href') || '').slice(0, 200),
      selector: selectorOf(el),
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    };
  });

  return {
    title: document.title,
    url: location.href,
    text_excerpt: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 4000),
    elements
  };
}
"""


class BrowserManager:
    def __init__(self, config: AgentConfig, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._connected_via = ""
        self._executable_path: str | None = None

    def availability(self) -> dict[str, Any]:
        available = sync_playwright is not None
        result = {
            "available": available,
            "connected": self._context is not None,
            "connected_via": self._connected_via or None,
            "browser_engine": self.config.browser_engine,
        }
        if not available:
            result["error"] = PLAYWRIGHT_IMPORT_ERROR or "playwright is not installed"
            return result

        if self._executable_path:
            result["executable_path"] = self._executable_path
            return result

        try:
            with sync_playwright() as playwright:
                browser_type = getattr(playwright, self.config.browser_engine)
                self._executable_path = browser_type.executable_path
                result["executable_path"] = self._executable_path
        except Exception as exc:  # pragma: no cover - depends on browser install
            result["error"] = str(exc)
        return result

    def launch(
        self,
        url: str | None = None,
        headless: bool | None = None,
    ) -> dict[str, Any]:
        if sync_playwright is None:
            return self.availability()
        if self._context is not None:
            return self.status(include_snapshot=True)

        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, self.config.browser_engine)
        launch_kwargs: dict[str, Any] = {}
        if self.config.browser_channel:
            launch_kwargs["channel"] = self.config.browser_channel
        if self.config.browser_executable_path:
            launch_kwargs["executable_path"] = self.config.browser_executable_path

        desired_headless = self.config.browser_headless if headless is None else headless
        self.config.browser_user_data_dir.mkdir(parents=True, exist_ok=True)
        self._context = browser_type.launch_persistent_context(
            user_data_dir=str(self.config.browser_user_data_dir),
            headless=desired_headless,
            viewport={"width": 1400, "height": 960},
            **launch_kwargs,
        )
        self._connected_via = "persistent_context"
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.bring_to_front()

        target_url = url or self.config.browser_start_url
        if target_url:
            self._page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=self.config.browser_timeout_ms,
            )
        return self.status(include_snapshot=True)

    def connect_cdp(self, endpoint_url: str) -> dict[str, Any]:
        if sync_playwright is None:
            return self.availability()
        if self._context is not None:
            return self.status(include_snapshot=True)

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            endpoint_url,
            timeout=self.config.browser_timeout_ms,
        )
        self._context = (
            self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.bring_to_front()
        self._connected_via = "cdp"
        return self.status(include_snapshot=True)

    def status(
        self,
        include_snapshot: bool = False,
        max_elements: int | None = None,
    ) -> dict[str, Any]:
        base = self.availability()
        if self._context is None:
            return base

        page = self._ensure_page()
        tabs = []
        for index, existing_page in enumerate(self._context.pages):
            tabs.append(
                {
                    "index": index,
                    "title": self._safe_page_title(existing_page),
                    "url": self._safe_page_url(existing_page),
                }
            )

        result = {
            **base,
            "connected": True,
            "connected_via": self._connected_via,
            "title": self._safe_page_title(page),
            "url": self._safe_page_url(page),
            "tabs": tabs,
        }
        if include_snapshot:
            try:
                result["snapshot"] = self.snapshot(
                    max_elements=max_elements or self.config.max_browser_elements
                )
            except Exception as exc:  # pragma: no cover - depends on page navigation timing
                result["snapshot_error"] = str(exc)
        return result

    def navigate(self, url: str) -> dict[str, Any]:
        page = self._ensure_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self.config.browser_timeout_ms)
        return self.status(include_snapshot=True)

    def snapshot(self, max_elements: int = 20) -> dict[str, Any]:
        page = self._ensure_page()
        return page.evaluate(DOM_SNAPSHOT_JS, max_elements)

    def click(
        self,
        selector: str | None = None,
        agent_id: str | None = None,
        text: str | None = None,
        index: int = 0,
        exact: bool = False,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        locator = self._resolve_locator(
            selector=selector,
            agent_id=agent_id,
            text=text,
            index=index,
            exact=exact,
        )
        locator.click(timeout=timeout_ms or self.config.browser_timeout_ms)
        return self.status(include_snapshot=True)

    def type_text(
        self,
        text: str,
        selector: str | None = None,
        agent_id: str | None = None,
        index: int = 0,
        clear: bool = True,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        locator = self._resolve_locator(
            selector=selector,
            agent_id=agent_id,
            index=index,
        )
        locator.click(timeout=timeout_ms or self.config.browser_timeout_ms)
        if clear:
            locator.fill(text, timeout=timeout_ms or self.config.browser_timeout_ms)
        else:
            locator.type(text, delay=20, timeout=timeout_ms or self.config.browser_timeout_ms)
        return self.status(include_snapshot=True)

    def press(self, key: str) -> dict[str, Any]:
        page = self._ensure_page()
        page.keyboard.press(key)
        return self.status(include_snapshot=False)

    def scroll(self, delta_x: int = 0, delta_y: int = 900) -> dict[str, Any]:
        page = self._ensure_page()
        page.mouse.wheel(delta_x, delta_y)
        return self.status(include_snapshot=True)

    def list_tabs(self) -> dict[str, Any]:
        return self.status(include_snapshot=False)

    def switch_tab(self, index: int) -> dict[str, Any]:
        if self._context is None:
            return self.status(include_snapshot=False)
        pages = self._context.pages
        if index < 0 or index >= len(pages):
            return {
                "connected": True,
                "error": f"tab index {index} is out of range",
                "tabs": [
                    {"index": i, "title": page.title(), "url": page.url}
                    for i, page in enumerate(pages)
                ],
            }
        self._page = pages[index]
        self._page.bring_to_front()
        return self.status(include_snapshot=True)

    def close(self) -> dict[str, Any]:
        result = {
            "connected": self._context is not None,
            "connected_via": self._connected_via or None,
        }
        try:
            if self._connected_via == "persistent_context" and self._context is not None:
                self._context.close()
            elif self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None
            self._connected_via = ""
        result["closed"] = True
        return result

    def _ensure_page(self):
        if self._context is None:
            raise RuntimeError(
                "Browser is not connected. Use browser_launch or browser_connect_cdp first."
            )
        if self._page is not None and not self._page.is_closed():
            return self._page
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self._page

    def _resolve_locator(
        self,
        selector: str | None = None,
        agent_id: str | None = None,
        text: str | None = None,
        index: int = 0,
        exact: bool = False,
    ):
        page = self._ensure_page()
        if agent_id:
            return page.locator(f'[data-desktop-agent-id="{agent_id}"]').nth(index)
        if selector:
            return page.locator(selector).nth(index)
        if text:
            return page.get_by_text(text, exact=exact).nth(index)
        raise ValueError("One of selector, agent_id, or text must be provided.")

    def _safe_page_title(self, page) -> str:
        try:
            return page.title()
        except Exception:  # pragma: no cover - navigation race
            return "<navigating>"

    def _safe_page_url(self, page) -> str:
        try:
            return page.url
        except Exception:  # pragma: no cover - navigation race
            return "<navigating>"


def browser_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "browser_launch",
                "description": "Launch a separate Playwright-managed browser with persistent profile storage for DOM-based automation. Use this only when no suitable browser window is already open, or when the user explicitly wants a separate browser session.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "headless": {"type": "boolean"},
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": "Set to true only when the user explicitly wants to force a separate Playwright browser even though an existing browser window is already open.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_connect_cdp",
                "description": "Attach to an existing Chrome or Chromium instance that was started with a remote debugging port.",
                "parameters": {
                    "type": "object",
                    "properties": {"endpoint_url": {"type": "string"}},
                    "required": ["endpoint_url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_status",
                "description": "Get browser status, tabs, current page metadata, and an optional DOM snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_snapshot": {"type": "boolean", "default": False},
                        "max_elements": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": "Navigate the active Playwright page to a URL.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_snapshot",
                "description": "Return a DOM snapshot of the active page, including visible interactive elements with stable agent ids.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_elements": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_click",
                "description": "Click an element using a DOM selector, agent id from browser_snapshot, or visible text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "agent_id": {"type": "string"},
                        "text": {"type": "string"},
                        "index": {"type": "integer", "minimum": 0, "default": 0},
                        "exact": {"type": "boolean", "default": False},
                        "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 60000},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_type",
                "description": "Type into an input using a DOM selector or agent id from browser_snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "selector": {"type": "string"},
                        "agent_id": {"type": "string"},
                        "clear": {"type": "boolean", "default": True},
                        "index": {"type": "integer", "minimum": 0, "default": 0},
                        "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 60000},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_press",
                "description": "Press a keyboard key in the active Playwright page.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_scroll",
                "description": "Scroll the active Playwright page.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delta_x": {"type": "integer", "default": 0},
                        "delta_y": {"type": "integer", "default": 900},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_list_tabs",
                "description": "List open browser tabs in the active Playwright context.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_switch_tab",
                "description": "Switch to a tab by zero-based index.",
                "parameters": {
                    "type": "object",
                    "properties": {"index": {"type": "integer", "minimum": 0}},
                    "required": ["index"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_close",
                "description": "Close the Playwright-managed browser session.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
