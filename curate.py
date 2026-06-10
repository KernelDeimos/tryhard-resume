#!/usr/bin/env python3
"""
curate.py — generate a job-specific resume directory under ./curated/

Usage:
    python curate.py < job_posting.txt
    echo "..." | python curate.py
    python curate.py --file job_posting.txt
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
CURATED_DIR = ROOT / "curated"

CLAUDE_PROMPT_TEMPLATE = """\
I need to curate a resume's data.yaml for a specific job posting.

JOB POSTING:
{job_posting}

CURRENT data.yaml:
{data_yaml}

Analyze the job posting and produce a curated version of data.yaml that highlights the most
relevant experience, skills, and projects for this specific role. Guidelines:
- Reorder experience entries so the most relevant ones appear first
- Rewrite or trim abstract bullets to speak directly to this role's needs
- Keep only the most relevant tagsections tags (you may remove tags that aren't relevant)
- Do not fabricate any information — only reorganize and emphasize what already exists
- Preserve valid YAML structure identical to the input

Output ONLY a JSON object with exactly these four string fields (no markdown, no extra text):
{{
  "company": "<company name, lowercase, hyphens for spaces, no special chars>",
  "position": "<job title, lowercase, hyphens for spaces, no special chars>",
  "data_yaml": "<the complete curated data.yaml as a string>",
  "cover_letter_prompt": "<cover letter guidance — see below>"
}}

For "cover_letter_prompt", write a practical advisory note (plain text, use newlines for readability)
addressed directly to the candidate. Cover at minimum:
- What to include: specific accomplishments, technologies, or themes from the resume that directly
  answer what the posting is asking for (be concrete, name the things)
- Tone: what register suits this company and role (e.g. formal/informal, confident/collaborative,
  technical depth vs. business outcomes)
- Opening and closing: how to hook the reader and what call-to-action fits
- What to avoid: red flags or mismatches that could undermine an otherwise strong application
- Any other observations about this particular posting that a thoughtful advisor would flag
  (e.g. a required qualification that is borderline, cultural signals in the job description, etc.)

"""


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text


BAR_WIDTH = 30
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _format_bar(tokens: int, estimated: int, elapsed: float) -> str:
    t = f"{elapsed:.0f}s"
    if tokens <= estimated:
        filled = int(BAR_WIDTH * tokens / estimated) if estimated else 0
        bar = "█" * filled + "░" * (BAR_WIDTH - filled)
        return f"  [{bar}] {tokens}/{estimated} tokens ({t})"
    else:
        new_estimate = int(tokens * 1.5)
        filled = int(BAR_WIDTH * tokens / new_estimate)
        bar = "█" * filled + "░" * (BAR_WIDTH - filled)
        return f"  [{bar}] {tokens} tokens, exceeded est. {estimated} (~{new_estimate} projected) ({t})"


def run_claude(prompt: str, estimated_tokens: int = 0) -> str:
    proc = subprocess.Popen(
        ["claude", "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()

    # Shared state updated by reader thread, read by animator thread.
    state = {"tokens": 0, "done": False}

    def animate():
        start = time.monotonic()
        frame = 0
        while not state["done"]:
            elapsed = time.monotonic() - start
            tokens = state["tokens"]
            if estimated_tokens and tokens > 0:
                line = _format_bar(tokens, estimated_tokens, elapsed)
            else:
                spin = SPINNER[frame % len(SPINNER)]
                line = f"  {spin} {elapsed:.0f}s elapsed"
            print(f"\r{line}", end="", file=sys.stderr, flush=True)
            frame += 1
            time.sleep(0.1)

    anim = threading.Thread(target=animate, daemon=True)
    anim.start()

    accumulated = ""
    final_result = None

    while True:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "assistant":
            content = event.get("message", {}).get("content", [])
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            if len(text) > len(accumulated):
                accumulated = text
                state["tokens"] = len(accumulated) // 4
        elif event.get("type") == "result":
            final_result = event.get("result", "")

    state["done"] = True
    anim.join()
    proc.wait()
    print(file=sys.stderr)

    if proc.returncode != 0:
        stderr = proc.stderr.read()
        print(f"claude stderr: {stderr}", file=sys.stderr)
        raise RuntimeError(f"claude exited with code {proc.returncode}")

    return (final_result or accumulated).strip()


def extract_json(raw: str) -> dict:
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to find a JSON object in the output
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise RuntimeError(f"Could not parse JSON from claude output: {e}\n\nRaw output:\n{raw}")


def main():
    parser = argparse.ArgumentParser(description="Curate a resume for a job posting using AI.")
    parser.add_argument("--file", "-f", help="Path to job posting text file (default: stdin)")
    args = parser.parse_args()

    if args.file:
        job_posting = Path(args.file).read_text()
    else:
        if sys.stdin.isatty():
            print("Paste the job posting text, then press Ctrl+D:", file=sys.stderr)
        job_posting = sys.stdin.read()

    if not job_posting.strip():
        sys.exit("Error: empty job posting")

    data_yaml = (ROOT / "data.yaml").read_text()

    prompt = CLAUDE_PROMPT_TEMPLATE.format(
        job_posting=job_posting.strip(),
        data_yaml=data_yaml,
    )

    estimated_tokens = len(data_yaml) // 4
    print(f"Generating curated resume with AI... (est. {estimated_tokens} tokens)", file=sys.stderr)
    raw = run_claude(prompt, estimated_tokens=estimated_tokens)

    parsed = extract_json(raw)
    company = slugify(parsed["company"])
    position = slugify(parsed["position"])
    curated_yaml = parsed["data_yaml"]
    cover_letter_prompt = parsed.get("cover_letter_prompt", "")

    today = date.today().strftime("%Y-%m-%d")
    dir_name = f"{today}_{company}_{position}"
    out_dir = CURATED_DIR / dir_name

    if out_dir.exists():
        print(f"Directory already exists: {out_dir}", file=sys.stderr)
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Aborted.")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True)
    print(f"Created: {out_dir}", file=sys.stderr)

    (out_dir / "data.yaml").write_text(curated_yaml)
    if cover_letter_prompt:
        (out_dir / "cover_letter_prompt.txt").write_text(cover_letter_prompt + "\n")

        cl_prompt = (
            cover_letter_prompt
            + "\n\n"
            "Write the cover letter now. Avoid stylistic markers associated with AI-generated text: "
            "no em-dashes, no emojis, no bullet points, no phrases like 'I am excited to' or "
            "'I would be a great fit'. Write in plain, direct prose with natural sentence variety."
        )
        print("Writing cover letter with AI... (est. 500 tokens)", file=sys.stderr)
        cover_letter = run_claude(cl_prompt, estimated_tokens=500)
        (out_dir / "cover_letter.txt").write_text(cover_letter + "\n")

    shutil.copy(ROOT / "resume.html", out_dir / "resume.html")
    shutil.copy(ROOT / "style.sass", out_dir / "style.sass")
    shutil.copytree(ROOT / "res", out_dir / "res")

    print("Compiling stylesheet...", file=sys.stderr)
    result = subprocess.run(
        ["npx", "sass", "style.sass:res/style.css"],
        cwd=out_dir,
    )
    if result.returncode != 0:
        sys.exit(f"sass exited with code {result.returncode}")

    print("Running onsave to generate index.html...", file=sys.stderr)
    onsave = Path.home() / "go" / "bin" / "onsave"
    result = subprocess.run(
        [str(onsave), "gotmpl", "resume.html", "data.yaml", "index.html"],
        cwd=out_dir,
    )
    if result.returncode != 0:
        sys.exit(f"onsave exited with code {result.returncode}")

    print(f"\nDone: {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
