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

Output ONLY a JSON object with exactly these three string fields (no markdown, no extra text):
{{
  "company": "<company name, lowercase, hyphens for spaces, no special chars>",
  "position": "<job title, lowercase, hyphens for spaces, no special chars>",
  "data_yaml": "<the complete curated data.yaml as a string>"
}}
"""


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text


def run_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("claude stderr:", result.stderr, file=sys.stderr)
        raise RuntimeError(f"claude exited with code {result.returncode}")
    return result.stdout.strip()


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

    print("Generating curated resume with AI...", file=sys.stderr)
    raw = run_claude(prompt)

    parsed = extract_json(raw)
    company = slugify(parsed["company"])
    position = slugify(parsed["position"])
    curated_yaml = parsed["data_yaml"]

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
