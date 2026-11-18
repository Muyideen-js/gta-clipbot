"""Caption writer for GitHub Actions. No Anthropic key needed.

Provider order:
  1. Any OpenAI-compatible endpoint you configure (LLM_BASE_URL + LLM_API_KEY +
     LLM_MODEL), e.g. Groq or Google Gemini's OpenAI-compatible endpoint, both
     with free tiers.
  2. GitHub Models, authenticated with the workflow's own GITHUB_TOKEN (needs
     `permissions: models: read`). Free, nothing to sign up for.
  3. Built-in templates, so a run never fails because of captions.

Frames from the clip are sent with the prompt when the model accepts images,
so titles describe what actually happens on screen.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import time

import requests

GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"

SYSTEM = (
    "You run packaging for a fast-growing GTA 6 YouTube channel. You write titles that make "
    "people stop scrolling: concrete (name the action you can see: a chase, a crash, a heist, "
    "a shootout, a stunt, a funny NPC), curiosity-driven, 40-80 characters, at most 2 emojis, "
    "no ALL-CAPS sentences, never clickbait the clip cannot back up. Descriptions open with a "
    "one-line hook, then one line of context, and end with a question that invites comments. "
    "Hashtags mix broad (GTA6, Gaming) and specific (GTA6Chase, ViceCity, GTA6Funny). "
    "Reply with JSON only."
)
# Search keywords added as YouTube tags (not shown to viewers).
KEYWORD_TAGS = ["gta 6", "gta vi", "grand theft auto 6", "gta 6 gameplay", "gta 6 clips",
                "gta 6 funny moments", "vice city", "leonida", "gta 6 shorts"]

TEMPLATES = [
    "This GTA 6 moment went completely wrong 💀",
    "Vice City police had no chance 🚔",
    "GTA 6 physics are actually unreal 🔥",
    "I did NOT expect that in GTA 6 😳",
    "The cleanest escape in GTA 6 so far 🏎️",
    "GTA 6 chaos in under 30 seconds 💥",
    "Leonida is wild at night 🌴",
]
BASE_TAGS = ["GTA6", "GTA6Clips", "Shorts", "Gaming", "GrandTheftAuto", "ViceCity"]


def _clean_title(t: str) -> str:
    t = re.sub(r"[<>]", "", t).strip().strip('"').strip()
    return t[:95]


LEAD_TAGS = ["GTA6", "Shorts", "GTAVI"]   # the first three show above the title


def _tags(raw) -> list[str]:
    """Big discovery tags first, then the clip-specific ones, then EXTRA_TAGS
    (e.g. the streamer's name). Capped at 8: YouTube ignores spammy hashtag lists."""
    extra = [t for t in (os.environ.get("EXTRA_TAGS") or "").split(",") if t.strip()]
    tags: list[str] = []
    for t in [*LEAD_TAGS, *(raw or []), *extra, *BASE_TAGS]:
        t = re.sub(r"[^A-Za-z0-9_]", "", str(t).lstrip("#"))
        if t and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    keep = tags[:7]
    for t in extra:   # always keep the streamer tag
        t = re.sub(r"[^A-Za-z0-9_]", "", t)
        if t and t.lower() not in {x.lower() for x in keep}:
            keep.append(t)
    return keep[:8]


def _template(context: dict) -> dict:
    if context.get("kind") == "long":
        mins = max(1, int(round(context["duration"] / 60)))
        return {"title": f"GTA 6 Best Moments: {mins} Minute{'s' if mins != 1 else ''} of Pure Chaos",
                "description": "The best moments from the stream, back to back.",
                "hashtags": _tags(["GTA6Gameplay"]), "thumb_text": "INSANE GTA 6 MOMENTS",
                "source": "template"}
    title = random.choice(TEMPLATES)
    return {
        "title": title,
        "description": "Raw GTA 6 gameplay highlight.",
        "hashtags": _tags([]),
        "thumb_text": " ".join(title.split()[:4]),
        "source": "template",
    }


def _providers() -> list[tuple[str, str, str, str]]:
    out = []
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        base = os.environ["LLM_BASE_URL"].rstrip("/")
        out.append(("custom", f"{base}/chat/completions", os.environ["LLM_API_KEY"],
                    os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")))
    if os.environ.get("GITHUB_TOKEN"):
        out.append(("github-models", GITHUB_MODELS_URL, os.environ["GITHUB_TOKEN"],
                    os.environ.get("CAPTION_MODEL") or "openai/gpt-4o-mini"))
    return out


def _ask(url: str, key: str, model: str, prompt: str, images: list[str]) -> str:
    content: list[dict] | str
    if images:
        content = [{"type": "text", "text": prompt}]
        for p in images:
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
    else:
        content = prompt
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": content}],
        "temperature": 0.9,
        "max_tokens": 600,
    }
    for attempt in range(3):
        r = requests.post(url, json=body, timeout=60,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
        if r.status_code == 429 and attempt < 2:
            time.sleep(int(r.headers.get("retry-after", "20")) + 1)
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("rate limited")


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in reply: {text[:200]}")
    data = json.loads(m.group(0))
    title = _clean_title(str(data.get("title", "")))
    if len(title) < 8:
        raise ValueError("title too short")
    words = title.split()
    return {
        "title": title,
        "description": str(data.get("description", "")).strip()[:1500],
        "hashtags": _tags(data.get("hashtags")),
        "thumb_text": str(data.get("thumb_text") or " ".join(words[:4])).strip()[:32],
    }


def write_caption(context: dict, frame_paths: list[str]) -> dict:
    """context: {source, start, duration, intensity}"""
    if context.get("kind") == "long":
        prompt = (
            f"Write the title for a {context['duration'] / 60:.0f}-minute YouTube video: the best moments "
            "of a GTA 6 live stream, back to back. The images are stills from those moments.\n"
            'Return JSON: {"title": "50-90 chars, specific and exciting", "description": "3-4 sentences", '
            '"hashtags": ["GTA6", "..."], "thumb_text": "2-4 WORD THUMBNAIL HOOK"} with 6-8 hashtags, no # signs.'
        )
        return _run_providers(prompt, frame_paths, context)
    prompt = (
        "Write a YouTube Shorts title for this GTA 6 gameplay clip.\n"
        f"Clip length: {context['duration']:.0f}s. Audio intensity: {context['intensity']}.\n"
        f"Source file name (may hint at the mission or activity): {context['source']}\n"
        + ("The attached images are stills from the start, middle and end of the clip. "
           "Base the title on what you can see.\n" if frame_paths else "")
        + ("Titles already used today (write something clearly different): "
           + " | ".join(context["avoid"][-12:]) + "\n" if context.get("avoid") else "")
        + 'Return JSON: {"title": "...", "description": "hook line, context line, question for comments", '
          '"hashtags": ["GTA6", "..."], "thumb_text": "2-4 word hook"} with 5-8 hashtags, no # signs.'
    )
    return _run_providers(prompt, frame_paths, context)


def _run_providers(prompt: str, frame_paths: list[str], context: dict) -> dict:
    for name, url, key, model in _providers():
        for imgs in ([frame_paths, []] if frame_paths else [[]]):
            try:
                result = _parse(_ask(url, key, model, prompt, imgs))
                result["source"] = f"{name}:{model}" + (":vision" if imgs else "")
                return result
            except Exception as exc:  # try text-only, then the next provider
                print(f"  caption via {name} ({'vision' if imgs else 'text'}) failed: {exc}")
    return _template(context)
